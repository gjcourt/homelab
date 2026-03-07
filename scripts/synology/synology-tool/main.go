// synology-tool: manage Synology NAS iSCSI storage for Kubernetes homelab.
//
// Subcommands:
//
//	audit            Cross-reference NAS LUNs and targets with Kubernetes PVs.
//	inspect <PV>     Show detailed LUN info (backing file, btrfs health, K8s PV).
//	copy <PV>        Copy a LUN backing file to the local machine via SCP.
//	cleanup-luns     Delete orphaned LUNs (no matching K8s PV) from the NAS.
//	cleanup-targets  Delete orphaned iSCSI targets (no matching LUN) from the NAS.
//	enable-targets   Enable all disabled iSCSI targets on the NAS.
//
// Environment variables:
//
//	SYNOLOGY_HOST      NAS hostname or IP  (required)
//	SYNOLOGY_USER      NAS user for SSH and DSM API (default: admin)
//	SYNOLOGY_PASSWORD  NAS password for SSH and DSM API (required)
//	SYNOLOGY_PORT      SSH port (default: 22)
//	SYNOLOGY_API_PORT  DSM HTTPS API port (default: 5001)
//
// kubectl must be on $PATH and configured for the target cluster.
package main

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"sort"
	"strings"
	"sync"

	"golang.org/x/crypto/ssh"
)

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

// APILUN represents an iSCSI LUN as returned by the Synology DSM API.
type APILUN struct {
	UUID     string `json:"uuid"`
	Name     string `json:"name"`
	Size     int64  `json:"size"`
	IsMapped bool   `json:"is_mapped"`
}

// SizeGiB returns the LUN size in gibibytes.
func (l APILUN) SizeGiB() float64 {
	return float64(l.Size) / (1024 * 1024 * 1024)
}

// APITarget represents an iSCSI target as returned by the Synology DSM API.
type APITarget struct {
	TargetID     int    `json:"target_id"`
	Name         string `json:"name"`
	IQN          string `json:"iqn"`
	IsEnabled    bool   `json:"is_enabled"`
	MappingIndex int    `json:"mapping_index"` // -1 when no LUN is mapped
}

// PV holds the Kubernetes PersistentVolume fields we care about.
type PV struct {
	Name      string
	Phase     string // Bound, Released, Available, …
	Namespace string
	Claim     string
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const (
	// maxTargets is the hard limit on iSCSI targets for Synology NAS.
	maxTargets = 128

	// synoiscsiwebapi is the path to the Synology iSCSI management CLI
	// (DSM 7.x). Used for LUN operations that require the local CLI.
	synoiscsiwebapi = "/usr/local/bin/synoiscsiwebapi"
)

// ---------------------------------------------------------------------------
// Environment helpers
// ---------------------------------------------------------------------------

// envOr returns the value of the environment variable key, or def if unset.
func envOr(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

// ---------------------------------------------------------------------------
// DSM API client (HTTPS)
// ---------------------------------------------------------------------------

// DSMClient communicates with the Synology DSM Web API over HTTPS.
// It handles authentication, session management, and iSCSI CRUD operations.
type DSMClient struct {
	baseURL string
	sid     string
	client  *http.Client
}

// newDSMClient creates and authenticates a DSM API client using the
// SYNOLOGY_* environment variables.
func newDSMClient() (*DSMClient, error) {
	host := envOr("SYNOLOGY_HOST", "")
	apiPort := envOr("SYNOLOGY_API_PORT", "5001")
	user := envOr("SYNOLOGY_USER", "admin")
	password := envOr("SYNOLOGY_PASSWORD", "")

	if host == "" {
		return nil, fmt.Errorf("SYNOLOGY_HOST is not set")
	}
	if password == "" {
		return nil, fmt.Errorf("SYNOLOGY_PASSWORD is not set")
	}

	d := &DSMClient{
		baseURL: fmt.Sprintf("https://%s:%s/webapi", host, apiPort),
		client: &http.Client{
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, //nolint:gosec
			},
		},
	}

	// Authenticate and obtain a session ID.
	params := url.Values{
		"api":     {"SYNO.API.Auth"},
		"version": {"6"},
		"method":  {"login"},
		"account": {user},
		"passwd":  {password},
	}
	var resp struct {
		Data struct {
			SID string `json:"sid"`
		} `json:"data"`
		Success bool `json:"success"`
	}
	if err := d.apiCall("auth.cgi", params, &resp); err != nil {
		return nil, fmt.Errorf("DSM login: %w", err)
	}
	if !resp.Success || resp.Data.SID == "" {
		return nil, fmt.Errorf("DSM login failed (check SYNOLOGY_USER / SYNOLOGY_PASSWORD)")
	}
	d.sid = resp.Data.SID
	return d, nil
}

// apiCall makes a GET request to the DSM API and decodes the JSON response.
// The session ID is appended automatically if set.
func (d *DSMClient) apiCall(endpoint string, params url.Values, out interface{}) error {
	if d.sid != "" {
		params.Set("_sid", d.sid)
	}
	reqURL := fmt.Sprintf("%s/%s?%s", d.baseURL, endpoint, params.Encode())
	resp, err := d.client.Get(reqURL)
	if err != nil {
		return fmt.Errorf("HTTP GET %s: %w", endpoint, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}
	return json.Unmarshal(body, out)
}

// apiCallRaw makes a GET request with a pre-built URL. Used when url.Values
// encoding does not match the required format (e.g. JSON-quoted target_id).
func (d *DSMClient) apiCallRaw(rawURL string, out interface{}) error {
	resp, err := d.client.Get(rawURL)
	if err != nil {
		return fmt.Errorf("HTTP GET: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}
	return json.Unmarshal(body, out)
}

// ListLUNs returns all iSCSI LUNs on the NAS.
func (d *DSMClient) ListLUNs() ([]APILUN, error) {
	params := url.Values{
		"api":     {"SYNO.Core.ISCSI.LUN"},
		"version": {"1"},
		"method":  {"list"},
	}
	var resp struct {
		Data struct {
			LUNs []APILUN `json:"luns"`
		} `json:"data"`
		Success bool `json:"success"`
	}
	if err := d.apiCall("entry.cgi", params, &resp); err != nil {
		return nil, err
	}
	if !resp.Success {
		return nil, fmt.Errorf("ListLUNs: API returned success=false")
	}
	return resp.Data.LUNs, nil
}

// ListTargets returns all iSCSI targets on the NAS.
func (d *DSMClient) ListTargets() ([]APITarget, error) {
	params := url.Values{
		"api":     {"SYNO.Core.ISCSI.Target"},
		"version": {"1"},
		"method":  {"list"},
	}
	var resp struct {
		Data struct {
			Targets []APITarget `json:"targets"`
		} `json:"data"`
		Success bool `json:"success"`
	}
	if err := d.apiCall("entry.cgi", params, &resp); err != nil {
		return nil, err
	}
	if !resp.Success {
		return nil, fmt.Errorf("ListTargets: API returned success=false")
	}
	return resp.Data.Targets, nil
}

// DeleteTarget deletes an iSCSI target by its numeric ID.
// The Synology DSM API requires target_id to be JSON-quoted in the URL,
// e.g. target_id="3" (URL-encoded as target_id=%223%22). Passing the bare
// integer causes error code 18990710.
func (d *DSMClient) DeleteTarget(targetID int) error {
	rawURL := fmt.Sprintf(
		"%s/entry.cgi?api=SYNO.Core.ISCSI.Target&version=1&method=delete&target_id=%%22%d%%22&_sid=%s",
		d.baseURL, targetID, d.sid,
	)
	var resp struct {
		Success bool `json:"success"`
		Error   struct {
			Code int `json:"code"`
		} `json:"error"`
	}
	if err := d.apiCallRaw(rawURL, &resp); err != nil {
		return err
	}
	if !resp.Success {
		return fmt.Errorf("API error code %d", resp.Error.Code)
	}
	return nil
}

// EnableTarget enables a disabled iSCSI target via the DSM API set method.
func (d *DSMClient) EnableTarget(targetID int) error {
	rawURL := fmt.Sprintf(
		"%s/entry.cgi?api=SYNO.Core.ISCSI.Target&version=1&method=set&target_id=%%22%d%%22&is_enabled=true&_sid=%s",
		d.baseURL, targetID, d.sid,
	)
	var resp struct {
		Success bool `json:"success"`
		Error   struct {
			Code int `json:"code"`
		} `json:"error"`
	}
	if err := d.apiCallRaw(rawURL, &resp); err != nil {
		return err
	}
	if !resp.Success {
		return fmt.Errorf("API error code %d", resp.Error.Code)
	}
	return nil
}

// ---------------------------------------------------------------------------
// SSH helpers
// ---------------------------------------------------------------------------

// newSSHClient creates an SSH client using SYNOLOGY_* environment variables.
func newSSHClient() (*ssh.Client, string, error) {
	host := envOr("SYNOLOGY_HOST", "")
	user := envOr("SYNOLOGY_USER", "admin")
	password := envOr("SYNOLOGY_PASSWORD", "")
	port := envOr("SYNOLOGY_PORT", "22")

	if host == "" {
		return nil, "", fmt.Errorf("SYNOLOGY_HOST is not set")
	}
	if password == "" {
		return nil, "", fmt.Errorf("SYNOLOGY_PASSWORD is not set")
	}

	cfg := &ssh.ClientConfig{
		User: user,
		Auth: []ssh.AuthMethod{ssh.Password(password)},
		// Intentionally insecure for homelab tooling; mirrors the CSI driver
		// which also skips host-key verification.
		HostKeyCallback: ssh.InsecureIgnoreHostKey(), //nolint:gosec
	}

	addr := fmt.Sprintf("%s:%s", host, port)
	client, err := ssh.Dial("tcp", addr, cfg)
	if err != nil {
		return nil, "", fmt.Errorf("SSH dial %s: %w", addr, err)
	}
	return client, password, nil
}

// sudoRun executes cmd on the remote NAS via sudo, feeding the password to
// stdin. Opens a fresh SSH session per call, so it is safe for concurrent
// use on the same *ssh.Client.
//
// Returns (exitCode, stdout, stderr, error).
func sudoRun(client *ssh.Client, password, cmd string) (int, string, string, error) {
	sess, err := client.NewSession()
	if err != nil {
		return -1, "", "", fmt.Errorf("new SSH session: %w", err)
	}
	defer sess.Close()

	var outBuf, errBuf bytes.Buffer
	sess.Stdout = &outBuf
	sess.Stderr = &errBuf

	wrapped := fmt.Sprintf("echo '%s' | sudo -S -p '' sh -c '%s'",
		strings.ReplaceAll(password, "'", "'\\''"),
		strings.ReplaceAll(cmd, "'", "'\\''"),
	)
	if err := sess.Run(wrapped); err != nil {
		exitCode := -1
		if exitErr, ok := err.(*ssh.ExitError); ok {
			exitCode = exitErr.ExitStatus()
		}
		return exitCode, outBuf.String(), errBuf.String(), err
	}
	return 0, outBuf.String(), errBuf.String(), nil
}

// ---------------------------------------------------------------------------
// Kubernetes PV helpers
// ---------------------------------------------------------------------------

// pvNameFromLUN converts a Synology LUN name to the expected Kubernetes PV
// name by stripping the "k8s-csi-" prefix added by the CSI driver.
func pvNameFromLUN(lunName string) string {
	return strings.TrimPrefix(lunName, "k8s-csi-")
}

// getK8sPVs calls kubectl to list all PVs and returns a map keyed by PV name.
func getK8sPVs() (map[string]PV, error) {
	out, err := exec.Command("kubectl", "get", "pv", "-o", "json").Output()
	if err != nil {
		return nil, fmt.Errorf("kubectl get pv: %w", err)
	}

	var list struct {
		Items []struct {
			Metadata struct {
				Name string `json:"name"`
			} `json:"metadata"`
			Spec struct {
				ClaimRef *struct {
					Namespace string `json:"namespace"`
					Name      string `json:"name"`
				} `json:"claimRef"`
			} `json:"spec"`
			Status struct {
				Phase string `json:"phase"`
			} `json:"status"`
		} `json:"items"`
	}
	if err := json.Unmarshal(out, &list); err != nil {
		return nil, fmt.Errorf("unmarshal PV list: %w", err)
	}

	pvs := make(map[string]PV, len(list.Items))
	for _, item := range list.Items {
		pv := PV{
			Name:  item.Metadata.Name,
			Phase: item.Status.Phase,
		}
		if item.Spec.ClaimRef != nil {
			pv.Namespace = item.Spec.ClaimRef.Namespace
			pv.Claim = item.Spec.ClaimRef.Name
		}
		pvs[pv.Name] = pv
	}
	return pvs, nil
}

// ---------------------------------------------------------------------------
// Resolution helpers
// ---------------------------------------------------------------------------

// findLUN looks up a LUN by either PV name (e.g. "pvc-abc123") or full LUN
// name (e.g. "k8s-csi-pvc-abc123").
func findLUN(luns []APILUN, nameOrPV string) (APILUN, bool) {
	lunName := nameOrPV
	if !strings.HasPrefix(nameOrPV, "k8s-csi-") {
		lunName = "k8s-csi-" + nameOrPV
	}
	for _, l := range luns {
		if l.Name == lunName {
			return l, true
		}
	}
	return APILUN{}, false
}

// findTarget looks up a target by its name (which matches the LUN name
// by convention in the Synology CSI driver).
func findTarget(targets []APITarget, lunName string) (APITarget, bool) {
	for _, t := range targets {
		if t.Name == lunName {
			return t, true
		}
	}
	return APITarget{}, false
}

// ---------------------------------------------------------------------------
// audit command
// ---------------------------------------------------------------------------

func cmdAudit() error {
	// Fetch K8s PVs and NAS data concurrently.
	type pvResult struct {
		pvs map[string]PV
		err error
	}
	type nasResult struct {
		luns    []APILUN
		targets []APITarget
		err     error
	}

	pvCh := make(chan pvResult, 1)
	nasCh := make(chan nasResult, 1)

	go func() {
		pvs, err := getK8sPVs()
		pvCh <- pvResult{pvs, err}
	}()

	go func() {
		dsm, err := newDSMClient()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		luns, err := dsm.ListLUNs()
		if err != nil {
			nasCh <- nasResult{err: fmt.Errorf("list LUNs: %w", err)}
			return
		}
		targets, err := dsm.ListTargets()
		if err != nil {
			nasCh <- nasResult{err: fmt.Errorf("list targets: %w", err)}
			return
		}
		nasCh <- nasResult{luns: luns, targets: targets}
	}()

	pvRes := <-pvCh
	nasRes := <-nasCh

	if pvRes.err != nil {
		return fmt.Errorf("Kubernetes PV fetch: %w", pvRes.err)
	}
	if nasRes.err != nil {
		return fmt.Errorf("NAS API: %w", nasRes.err)
	}

	pvs := pvRes.pvs
	luns := nasRes.luns
	targets := nasRes.targets

	// Build LUN name set for target orphan detection.
	lunNames := make(map[string]bool, len(luns))
	for _, l := range luns {
		lunNames[l.Name] = true
	}

	// --- LUN Report ---
	type row struct {
		name    string
		status  string
		sizeGiB float64
		claim   string
	}
	var rows []row

	for _, lun := range luns {
		pvName := pvNameFromLUN(lun.Name)
		pv, found := pvs[pvName]

		var status, claim string
		switch {
		case !found:
			status = "ORPHAN"
		case pv.Phase == "Bound":
			status = "Bound"
			if pv.Namespace != "" && pv.Claim != "" {
				claim = pv.Namespace + "/" + pv.Claim
			}
		default:
			status = pv.Phase
		}
		rows = append(rows, row{lun.Name, status, lun.SizeGiB(), claim})
	}

	sort.Slice(rows, func(i, j int) bool {
		if rows[i].status != rows[j].status {
			return rows[i].status < rows[j].status
		}
		return rows[i].name < rows[j].name
	})

	counts := map[string]int{}
	fmt.Printf("%-60s  %-10s  %8s  %s\n", "LUN Name", "Status", "Size(GiB)", "Claim")
	fmt.Println(strings.Repeat("-", 100))
	for _, r := range rows {
		counts[r.status]++
		fmt.Printf("%-60s  %-10s  %8.2f  %s\n", r.name, r.status, r.sizeGiB, r.claim)
	}
	fmt.Println(strings.Repeat("-", 100))
	fmt.Printf("Total LUNs: %d  |  Bound: %d  |  Released: %d  |  Orphaned: %d\n",
		len(rows), counts["Bound"], counts["Released"], counts["ORPHAN"])

	// --- Target Summary ---
	fmt.Println()
	var orphanTargets, disabledTargets int
	for _, t := range targets {
		if !lunNames[t.Name] {
			orphanTargets++
		}
		if !t.IsEnabled {
			disabledTargets++
		}
	}
	fmt.Printf("Targets: %d/%d (limit %d)  |  Orphaned: %d  |  Disabled: %d\n",
		len(targets), maxTargets, maxTargets, orphanTargets, disabledTargets)

	if len(targets) > maxTargets-10 {
		fmt.Printf("WARNING: target count %d is close to the %d limit. Run 'cleanup-targets' to free slots.\n",
			len(targets), maxTargets)
	}
	if orphanTargets > 0 {
		fmt.Printf("  %d orphaned target(s) found. Run 'cleanup-targets' to remove.\n", orphanTargets)
	}
	if disabledTargets > 0 {
		fmt.Printf("  %d disabled target(s) found. Run 'enable-targets' to re-enable.\n", disabledTargets)
	}

	return nil
}

// ---------------------------------------------------------------------------
// inspect command
// ---------------------------------------------------------------------------

func cmdInspect(nameOrPV string) error {
	// Fetch NAS data and K8s PVs concurrently.
	type nasResult struct {
		luns    []APILUN
		targets []APITarget
		err     error
	}
	type pvResult struct {
		pvs map[string]PV
		err error
	}

	nasCh := make(chan nasResult, 1)
	pvCh := make(chan pvResult, 1)

	go func() {
		dsm, err := newDSMClient()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		luns, err := dsm.ListLUNs()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		targets, err := dsm.ListTargets()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		nasCh <- nasResult{luns: luns, targets: targets}
	}()

	go func() {
		pvs, err := getK8sPVs()
		pvCh <- pvResult{pvs, err}
	}()

	nasRes := <-nasCh
	pvRes := <-pvCh

	if nasRes.err != nil {
		return fmt.Errorf("NAS API: %w", nasRes.err)
	}
	if pvRes.err != nil {
		return fmt.Errorf("K8s PVs: %w", pvRes.err)
	}

	lun, found := findLUN(nasRes.luns, nameOrPV)
	if !found {
		return fmt.Errorf("LUN not found for %q", nameOrPV)
	}
	target, hasTarget := findTarget(nasRes.targets, lun.Name)
	pvName := pvNameFromLUN(lun.Name)
	pv := pvRes.pvs[pvName]

	// --- LUN ---
	fmt.Println("=== LUN ===")
	fmt.Printf("  Name:   %s\n", lun.Name)
	fmt.Printf("  UUID:   %s\n", lun.UUID)
	fmt.Printf("  Size:   %.2f GiB\n", lun.SizeGiB())
	fmt.Printf("  Mapped: %v\n", lun.IsMapped)

	// --- Target ---
	fmt.Println("\n=== Target ===")
	if hasTarget {
		fmt.Printf("  Name:    %s\n", target.Name)
		fmt.Printf("  ID:      %d\n", target.TargetID)
		fmt.Printf("  IQN:     %s\n", target.IQN)
		fmt.Printf("  Enabled: %v\n", target.IsEnabled)
	} else {
		fmt.Println("  (no target found)")
	}

	// --- K8s PV ---
	fmt.Println("\n=== Kubernetes PV ===")
	if pv.Name != "" {
		fmt.Printf("  PV:     %s\n", pv.Name)
		fmt.Printf("  Phase:  %s\n", pv.Phase)
		if pv.Namespace != "" && pv.Claim != "" {
			fmt.Printf("  Claim:  %s/%s\n", pv.Namespace, pv.Claim)
		}
	} else {
		fmt.Printf("  PV %s not found in cluster (orphaned)\n", pvName)
	}

	// --- Backing File (via SSH) ---
	client, password, err := newSSHClient()
	if err != nil {
		fmt.Printf("\n(SSH unavailable; skipping backing file inspection: %v)\n", err)
		return nil
	}
	defer client.Close()

	backingDir := fmt.Sprintf("/volume1/@iSCSI/LUN/BLUN/%s", lun.UUID)
	backingFile := fmt.Sprintf("%s/%s_00000", backingDir, lun.Name)

	fmt.Println("\n=== Backing File ===")
	fmt.Printf("  Path: %s\n", backingFile)

	_, lsOut, _, err := sudoRun(client, password, fmt.Sprintf("ls -la %s", backingDir))
	if err != nil {
		fmt.Printf("  (could not list directory: %v)\n", err)
	} else {
		for _, line := range strings.Split(strings.TrimSpace(lsOut), "\n") {
			fmt.Printf("  %s\n", line)
		}
	}

	// --- Btrfs Check (via loop device on NAS) ---
	fmt.Println("\n=== Btrfs Check ===")

	_, loopDev, loopStderr, err := sudoRun(client, password,
		fmt.Sprintf("losetup --find --show %s", backingFile))
	loopDev = strings.TrimSpace(loopDev)
	if err != nil || loopDev == "" {
		fmt.Printf("  (could not set up loop device: %v — %s)\n",
			err, strings.TrimSpace(loopStderr))
		return nil
	}
	fmt.Printf("  Loop: %s\n", loopDev)

	defer func() {
		_, _, _, _ = sudoRun(client, password, fmt.Sprintf("losetup -d %s", loopDev))
		fmt.Printf("  (loop device %s detached)\n", loopDev)
	}()

	code, checkOut, checkStderr, err := sudoRun(client, password,
		fmt.Sprintf("btrfs check --force --readonly %s", loopDev))
	if err != nil && code < 0 {
		fmt.Printf("  (btrfs check failed to run: %v)\n", err)
		return nil
	}
	for _, line := range strings.Split(strings.TrimSpace(checkOut), "\n") {
		if line != "" {
			fmt.Printf("  %s\n", line)
		}
	}
	for _, line := range strings.Split(strings.TrimSpace(checkStderr), "\n") {
		if line != "" && !strings.Contains(strings.ToLower(line), "password") {
			fmt.Printf("  %s\n", line)
		}
	}
	if code == 0 {
		fmt.Println("  Result: OK")
	} else {
		fmt.Printf("  Result: ERRORS DETECTED (exit code %d)\n", code)
	}

	return nil
}

// ---------------------------------------------------------------------------
// copy command
// ---------------------------------------------------------------------------

func cmdCopy(nameOrPV, output string) error {
	dsm, err := newDSMClient()
	if err != nil {
		return err
	}

	luns, err := dsm.ListLUNs()
	if err != nil {
		return fmt.Errorf("list LUNs: %w", err)
	}

	lun, found := findLUN(luns, nameOrPV)
	if !found {
		return fmt.Errorf("LUN not found for %q", nameOrPV)
	}

	backingFile := fmt.Sprintf("/volume1/@iSCSI/LUN/BLUN/%s/%s_00000", lun.UUID, lun.Name)
	if output == "" {
		output = lun.Name + ".img"
	}

	host := envOr("SYNOLOGY_HOST", "")
	user := envOr("SYNOLOGY_USER", "admin")
	password := envOr("SYNOLOGY_PASSWORD", "")
	port := envOr("SYNOLOGY_PORT", "22")

	fmt.Printf("LUN:    %s\n", lun.Name)
	fmt.Printf("UUID:   %s\n", lun.UUID)
	fmt.Printf("Size:   %.2f GiB\n", lun.SizeGiB())
	fmt.Printf("Source: %s:%s\n", host, backingFile)
	fmt.Printf("Dest:   %s\n\n", output)

	scpCmd := exec.Command("sshpass", "-p", password,
		"scp",
		"-P", port,
		"-o", "StrictHostKeyChecking=no",
		fmt.Sprintf("%s@%s:%s", user, host, backingFile),
		output,
	)
	scpCmd.Stdout = os.Stdout
	scpCmd.Stderr = os.Stderr

	fmt.Println("Copying...")
	if err := scpCmd.Run(); err != nil {
		return fmt.Errorf("scp: %w", err)
	}

	info, err := os.Stat(output)
	if err != nil {
		return fmt.Errorf("stat %s: %w", output, err)
	}

	fmt.Printf("\nDone. Copied %.2f GiB to %s\n",
		float64(info.Size())/(1024*1024*1024), output)
	return nil
}

// ---------------------------------------------------------------------------
// cleanup-luns command
// ---------------------------------------------------------------------------

// deleteLUN unmaps the LUN from its target, deletes the LUN, then deletes
// the target via the synoiscsiwebapi CLI over SSH. Each step opens its own
// SSH session so this func is safe for concurrent use on the same *ssh.Client.
func deleteLUN(client *ssh.Client, password, uuid, tid string) error {
	type step struct {
		desc string
		cmd  string
		skip bool
	}

	steps := []step{
		{
			desc: "unmap LUN from target",
			cmd:  fmt.Sprintf("%s lun unmap_target %s %s", synoiscsiwebapi, uuid, tid),
			skip: tid == "",
		},
		{
			desc: "delete LUN",
			cmd:  fmt.Sprintf("%s lun delete %s", synoiscsiwebapi, uuid),
		},
		{
			desc: "delete target",
			cmd:  fmt.Sprintf("%s target delete %s", synoiscsiwebapi, tid),
			skip: tid == "",
		},
	}

	for _, s := range steps {
		if s.skip {
			continue
		}
		code, _, errOut, err := sudoRun(client, password, s.cmd)
		if err != nil || code != 0 {
			return fmt.Errorf("%s (exit %d): %w — %s",
				s.desc, code, err, strings.TrimSpace(errOut))
		}
	}
	return nil
}

func cmdCleanupLUNs(dryRun bool, workers int) error {
	// Fetch NAS data and K8s PVs concurrently.
	type nasResult struct {
		luns    []APILUN
		targets []APITarget
		err     error
	}
	type pvResult struct {
		pvs map[string]PV
		err error
	}

	nasCh := make(chan nasResult, 1)
	pvCh := make(chan pvResult, 1)

	go func() {
		dsm, err := newDSMClient()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		luns, err := dsm.ListLUNs()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		targets, err := dsm.ListTargets()
		if err != nil {
			nasCh <- nasResult{err: err}
			return
		}
		nasCh <- nasResult{luns: luns, targets: targets}
	}()

	go func() {
		pvs, err := getK8sPVs()
		pvCh <- pvResult{pvs, err}
	}()

	nasRes := <-nasCh
	pvRes := <-pvCh

	if nasRes.err != nil {
		return fmt.Errorf("NAS API: %w", nasRes.err)
	}
	if pvRes.err != nil {
		return fmt.Errorf("K8s PVs: %w", pvRes.err)
	}

	pvs := pvRes.pvs

	// Build name→target map for cross-referencing.
	targetByName := make(map[string]APITarget, len(nasRes.targets))
	for _, t := range nasRes.targets {
		targetByName[t.Name] = t
	}

	// Find orphaned LUNs (no matching Kubernetes PV).
	type orphan struct {
		Name     string
		UUID     string
		TargetID string // empty string if no target exists
	}
	var orphans []orphan
	for _, lun := range nasRes.luns {
		pvName := pvNameFromLUN(lun.Name)
		if _, found := pvs[pvName]; !found {
			tid := ""
			if t, ok := targetByName[lun.Name]; ok {
				tid = fmt.Sprintf("%d", t.TargetID)
			}
			orphans = append(orphans, orphan{lun.Name, lun.UUID, tid})
		}
	}
	sort.Slice(orphans, func(i, j int) bool { return orphans[i].Name < orphans[j].Name })

	if len(orphans) == 0 {
		fmt.Println("No orphaned LUNs found.")
		return nil
	}

	if dryRun {
		fmt.Printf("DRY RUN — would delete %d orphaned LUN(s):\n", len(orphans))
		for _, o := range orphans {
			fmt.Printf("  LUN %-50s  UUID %s  TID %s\n", o.Name, o.UUID, o.TargetID)
		}
		fmt.Println("\nRe-run without --dry-run to delete.")
		return nil
	}

	// SSH for synoiscsiwebapi LUN deletion.
	client, password, err := newSSHClient()
	if err != nil {
		return fmt.Errorf("SSH: %w", err)
	}
	defer client.Close()

	if workers < 1 {
		workers = 1
	}
	fmt.Printf("Deleting %d orphaned LUN(s) with %d worker(s)...\n", len(orphans), workers)

	type result struct {
		Name string
		TID  string
		Err  error
	}

	jobs := make(chan orphan, len(orphans))
	results := make(chan result, len(orphans))

	var wg sync.WaitGroup
	for range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for o := range jobs {
				err := deleteLUN(client, password, o.UUID, o.TargetID)
				results <- result{o.Name, o.TargetID, err}
			}
		}()
	}

	for _, o := range orphans {
		jobs <- o
	}
	close(jobs)

	go func() { wg.Wait(); close(results) }()

	var delResults []result
	for r := range results {
		delResults = append(delResults, r)
	}
	sort.Slice(delResults, func(i, j int) bool { return delResults[i].Name < delResults[j].Name })

	ok, failed := 0, 0
	for _, r := range delResults {
		if r.Err != nil {
			fmt.Printf("  FAIL  %s  TID %s: %v\n", r.Name, r.TID, r.Err)
			failed++
		} else {
			fmt.Printf("  OK    %s  TID %s\n", r.Name, r.TID)
			ok++
		}
	}

	fmt.Printf("\nDeleted %d/%d  |  Failed %d/%d\n", ok, len(orphans), failed, len(orphans))
	return nil
}

// ---------------------------------------------------------------------------
// cleanup-targets command
// ---------------------------------------------------------------------------

func cmdCleanupTargets(dryRun bool) error {
	dsm, err := newDSMClient()
	if err != nil {
		return err
	}

	luns, err := dsm.ListLUNs()
	if err != nil {
		return fmt.Errorf("list LUNs: %w", err)
	}
	targets, err := dsm.ListTargets()
	if err != nil {
		return fmt.Errorf("list targets: %w", err)
	}

	// Orphaned target = target whose name does not match any existing LUN.
	lunNames := make(map[string]bool, len(luns))
	for _, l := range luns {
		lunNames[l.Name] = true
	}

	var orphans []APITarget
	for _, t := range targets {
		if !lunNames[t.Name] {
			orphans = append(orphans, t)
		}
	}
	sort.Slice(orphans, func(i, j int) bool { return orphans[i].TargetID < orphans[j].TargetID })

	if len(orphans) == 0 {
		fmt.Printf("No orphaned targets found (%d targets, %d LUNs).\n", len(targets), len(luns))
		return nil
	}

	fmt.Printf("Found %d orphaned target(s) (of %d total, %d LUNs).\n",
		len(orphans), len(targets), len(luns))

	if dryRun {
		fmt.Println("\nDRY RUN — would delete:")
		for _, t := range orphans {
			fmt.Printf("  TID %-4d  %s\n", t.TargetID, t.Name)
		}
		fmt.Println("\nRe-run without --dry-run to delete.")
		return nil
	}

	fmt.Printf("\nDeleting %d orphaned target(s)...\n", len(orphans))
	ok, failed := 0, 0
	for i, t := range orphans {
		if err := dsm.DeleteTarget(t.TargetID); err != nil {
			fmt.Printf("  FAIL TID %d (%s): %v\n", t.TargetID, t.Name, err)
			failed++
		} else {
			ok++
		}
		if (i+1)%10 == 0 || i == len(orphans)-1 {
			fmt.Printf("  Progress: %d/%d (ok=%d, fail=%d)\n", i+1, len(orphans), ok, failed)
		}
	}

	fmt.Printf("\nDeleted %d/%d  |  Failed %d/%d\n", ok, len(orphans), failed, len(orphans))
	fmt.Printf("Targets remaining: %d/%d\n", len(targets)-ok, maxTargets)
	return nil
}

// ---------------------------------------------------------------------------
// enable-targets command
// ---------------------------------------------------------------------------

func cmdEnableTargets(dryRun bool) error {
	dsm, err := newDSMClient()
	if err != nil {
		return err
	}

	targets, err := dsm.ListTargets()
	if err != nil {
		return fmt.Errorf("list targets: %w", err)
	}

	var disabled []APITarget
	for _, t := range targets {
		if !t.IsEnabled {
			disabled = append(disabled, t)
		}
	}

	if len(disabled) == 0 {
		fmt.Printf("All %d target(s) are enabled.\n", len(targets))
		return nil
	}

	fmt.Printf("Found %d disabled target(s) (of %d total).\n", len(disabled), len(targets))

	if dryRun {
		fmt.Println("\nDRY RUN — would enable:")
		for _, t := range disabled {
			fmt.Printf("  TID %-4d  %s\n", t.TargetID, t.Name)
		}
		fmt.Println("\nRe-run without --dry-run to enable.")
		return nil
	}

	fmt.Printf("\nEnabling %d target(s)...\n", len(disabled))
	ok, failed := 0, 0
	for _, t := range disabled {
		if err := dsm.EnableTarget(t.TargetID); err != nil {
			fmt.Printf("  FAIL TID %d (%s): %v\n", t.TargetID, t.Name, err)
			failed++
		} else {
			fmt.Printf("  OK   TID %d (%s)\n", t.TargetID, t.Name)
			ok++
		}
	}

	fmt.Printf("\nEnabled %d/%d  |  Failed %d/%d\n", ok, len(disabled), failed, len(disabled))
	return nil
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

func usage() {
	fmt.Fprintf(os.Stderr, `synology-tool -- manage Synology NAS iSCSI storage for Kubernetes

USAGE
  synology-tool <command> [flags]

COMMANDS
  audit            Cross-reference NAS LUNs and targets with Kubernetes PVs.
  inspect <PV>     Show detailed LUN info (backing file, btrfs health, K8s PV).
  copy <PV>        Copy a LUN backing file to the local machine via SCP.
  cleanup-luns     Delete orphaned LUNs (no matching K8s PV) from the NAS.
  cleanup-targets  Delete orphaned iSCSI targets (no matching LUN) from the NAS.
  enable-targets   Enable all disabled iSCSI targets on the NAS.

FLAGS
  inspect / copy:
    <PV>              PV name (pvc-xxx) or LUN name (k8s-csi-pvc-xxx).

  copy:
    --output PATH     Local destination path (default: ./<lun-name>.img).

  cleanup-luns:
    --dry-run         Preview deletions without making changes.
    --workers N       Concurrent deletion workers (default: 1).

  cleanup-targets / enable-targets:
    --dry-run         Preview without making changes.

ENVIRONMENT
  SYNOLOGY_HOST      NAS hostname or IP  (required)
  SYNOLOGY_USER      NAS user            (default: admin)
  SYNOLOGY_PASSWORD  NAS password        (required)
  SYNOLOGY_PORT      SSH port            (default: 22)
  SYNOLOGY_API_PORT  DSM HTTPS API port  (default: 5001)

kubectl must be on $PATH and configured for the target cluster.
`)
}

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "audit":
		fs := flag.NewFlagSet("audit", flag.ExitOnError)
		_ = fs.Parse(os.Args[2:])
		if err := cmdAudit(); err != nil {
			fmt.Fprintf(os.Stderr, "audit: %v\n", err)
			os.Exit(1)
		}

	case "inspect":
		fs := flag.NewFlagSet("inspect", flag.ExitOnError)
		_ = fs.Parse(os.Args[2:])
		if fs.NArg() != 1 {
			fmt.Fprintln(os.Stderr, "inspect requires one argument: PV name or LUN name")
			os.Exit(1)
		}
		if err := cmdInspect(fs.Arg(0)); err != nil {
			fmt.Fprintf(os.Stderr, "inspect: %v\n", err)
			os.Exit(1)
		}

	case "copy":
		fs := flag.NewFlagSet("copy", flag.ExitOnError)
		output := fs.String("output", "", "Local destination path (default: ./<lun-name>.img)")
		_ = fs.Parse(os.Args[2:])
		if fs.NArg() != 1 {
			fmt.Fprintln(os.Stderr, "copy requires one argument: PV name or LUN name")
			os.Exit(1)
		}
		if err := cmdCopy(fs.Arg(0), *output); err != nil {
			fmt.Fprintf(os.Stderr, "copy: %v\n", err)
			os.Exit(1)
		}

	case "cleanup-luns":
		fs := flag.NewFlagSet("cleanup-luns", flag.ExitOnError)
		dryRun := fs.Bool("dry-run", false, "Preview deletions without making changes")
		workers := fs.Int("workers", 1, "Concurrent deletion workers")
		_ = fs.Parse(os.Args[2:])
		if err := cmdCleanupLUNs(*dryRun, *workers); err != nil {
			fmt.Fprintf(os.Stderr, "cleanup-luns: %v\n", err)
			os.Exit(1)
		}

	case "cleanup-targets":
		fs := flag.NewFlagSet("cleanup-targets", flag.ExitOnError)
		dryRun := fs.Bool("dry-run", false, "Preview deletions without making changes")
		_ = fs.Parse(os.Args[2:])
		if err := cmdCleanupTargets(*dryRun); err != nil {
			fmt.Fprintf(os.Stderr, "cleanup-targets: %v\n", err)
			os.Exit(1)
		}

	case "enable-targets":
		fs := flag.NewFlagSet("enable-targets", flag.ExitOnError)
		dryRun := fs.Bool("dry-run", false, "Preview without making changes")
		_ = fs.Parse(os.Args[2:])
		if err := cmdEnableTargets(*dryRun); err != nil {
			fmt.Fprintf(os.Stderr, "enable-targets: %v\n", err)
			os.Exit(1)
		}

	case "help", "-h", "--help":
		usage()

	default:
		fmt.Fprintf(os.Stderr, "unknown command: %q\n\n", os.Args[1])
		usage()
		os.Exit(1)
	}
}
