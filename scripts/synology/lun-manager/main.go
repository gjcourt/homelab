// lun-manager: audit and clean up orphaned Synology iSCSI LUNs/Targets.
//
// Subcommands:
//
//		audit   - Compare NAS iSCSI config against Kubernetes PVs and report
//		          bound/released/orphaned LUNs.
//		cleanup - Delete orphaned LUNs and their targets from the NAS.
//	            Pass --dry-run to preview without making changes.
//
// Required environment variables:
//
//	SYNOLOGY_HOST     - NAS hostname or IP (e.g. "nas.example.com")
//	SYNOLOGY_USER     - SSH user (e.g. "admin")
//	SYNOLOGY_PASSWORD - SSH/sudo password
//
// Optional:
//
//	SYNOLOGY_PORT     - SSH port (default: 22)
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"sync"

	"golang.org/x/crypto/ssh"
)

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

// LUN represents a single Synology iSCSI LUN entry from iscsi_lun.conf.
type LUN struct {
	Name    string
	UUID    string
	SizeGiB float64
}

// Target represents a Synology iSCSI Target entry from iscsi_target.conf.
type Target struct {
	Name string
	TID  string
}

// PV holds the Kubernetes PersistentVolume fields we care about.
type PV struct {
	Name      string
	Phase     string // Bound, Released, Available, …
	Namespace string
	Claim     string
}

// NASConfig holds the complete iSCSI configuration read from the NAS.
type NASConfig struct {
	LUNs    []LUN
	Targets []Target
	// byLUNName maps LUN name → Target (via the mapping config).
	byLUNName map[string]Target
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// envOr returns the value of the environment variable key, or def if unset.
func envOr(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

// parseINI parses a Synology-style INI config file (multiple sections of
// [HEADER] lines followed by key=value pairs on the same header line or
// on separate lines).
//
// Synology's format looks like:
//
//	[iSCSI_LUN_1] uuid=abc123 name=k8s-csi-xxx size=10737418240 ...
//
// Each invocation of the section header produces one map entry.
func parseINI(text string) []map[string]string {
	var sections []map[string]string
	var current map[string]string

	scanner := bufio.NewScanner(strings.NewReader(text))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			// New section — save previous and start fresh.
			if current != nil {
				sections = append(sections, current)
			}
			current = map[string]string{}
			// Remainder of the line after ']' may contain key=value pairs.
			end := strings.Index(line, "]")
			if end >= 0 && end+1 < len(line) {
				rest := strings.TrimSpace(line[end+1:])
				for _, token := range strings.Fields(rest) {
					if k, v, ok := strings.Cut(token, "="); ok {
						current[k] = v
					}
				}
			}
			continue
		}
		// key=value line inside a section.
		if current != nil {
			if k, v, ok := strings.Cut(line, "="); ok {
				current[strings.TrimSpace(k)] = strings.TrimSpace(v)
			}
		}
	}
	if current != nil {
		sections = append(sections, current)
	}
	return sections
}

// ---------------------------------------------------------------------------
// SSH helpers
// ---------------------------------------------------------------------------

// newSSHClient creates an SSH client using environment-variable credentials.
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
		Auth: []ssh.AuthMethod{
			ssh.Password(password),
		},
		// Intentionally insecure for homelab tooling; mirrors the Python
		// scripts which also skip host-key verification.
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
// stdin.  It opens a fresh session every call so it is safe to call from
// multiple goroutines on the same *ssh.Client.
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

	// Feed password via stdin so sudo -S can read it.
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

// catFile reads the contents of a remote file via sudo cat.
func catFile(client *ssh.Client, password, path string) (string, error) {
	_, out, errOut, err := sudoRun(client, password, "cat "+path)
	if err != nil {
		return "", fmt.Errorf("cat %s: %w (stderr: %s)", path, err, errOut)
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// NAS config loading (concurrent)
// ---------------------------------------------------------------------------

// loadNASConfig reads the three iSCSI config files concurrently and returns
// a populated NASConfig.
func loadNASConfig(client *ssh.Client, password string) (*NASConfig, error) {
	const (
		lunConf     = "/usr/syno/etc/iscsi_lun.conf"
		targetConf  = "/usr/syno/etc/iscsi_target.conf"
		mappingConf = "/usr/syno/etc/iscsi_mapping.conf"
	)

	type fileResult struct {
		name string
		text string
		err  error
	}

	files := []string{lunConf, targetConf, mappingConf}
	ch := make(chan fileResult, len(files))

	for _, f := range files {
		f := f
		go func() {
			text, err := catFile(client, password, f)
			ch <- fileResult{f, text, err}
		}()
	}

	results := map[string]string{}
	for range len(files) {
		r := <-ch
		if r.err != nil {
			return nil, fmt.Errorf("loading %s: %w", r.name, r.err)
		}
		results[r.name] = r.text
	}

	// Parse LUNs.
	var luns []LUN
	for _, m := range parseINI(results[lunConf]) {
		name := m["name"]
		uuid := m["uuid"]
		if name == "" || uuid == "" {
			continue
		}
		var sizeGiB float64
		if sizeStr := m["size"]; sizeStr != "" {
			if sizeBytes, err := strconv.ParseFloat(sizeStr, 64); err == nil {
				sizeGiB = sizeBytes / (1024 * 1024 * 1024)
			}
		}
		luns = append(luns, LUN{Name: name, UUID: uuid, SizeGiB: sizeGiB})
	}

	// Parse Targets.
	var targets []Target
	for _, m := range parseINI(results[targetConf]) {
		name := m["name"]
		tid := m["tid"]
		if name == "" || tid == "" {
			continue
		}
		targets = append(targets, Target{Name: name, TID: tid})
	}

	// Build mapping: LUN name → Target (via mapping config).
	// The mapping file links target IQN → LUN UUID; we cross-reference to
	// build a LUN-name → Target map.
	uuidToLUNName := map[string]string{}
	for _, l := range luns {
		uuidToLUNName[l.UUID] = l.Name
	}
	iqnToTarget := map[string]Target{}
	for _, t := range targets {
		iqnToTarget[t.Name] = t
	}

	byLUNName := map[string]Target{}
	for _, m := range parseINI(results[mappingConf]) {
		iqn := m["target_iqn"]
		lunUUID := m["lun_uuid"]
		if iqn == "" || lunUUID == "" {
			continue
		}
		lunName, ok := uuidToLUNName[lunUUID]
		if !ok {
			continue
		}
		if t, ok := iqnToTarget[iqn]; ok {
			byLUNName[lunName] = t
		}
	}

	return &NASConfig{
		LUNs:      luns,
		Targets:   targets,
		byLUNName: byLUNName,
	}, nil
}

// ---------------------------------------------------------------------------
// Kubernetes PV loading
// ---------------------------------------------------------------------------

// pvNameFromLUN converts a Synology LUN name to the expected Kubernetes PV
// name (strips the "k8s-csi-" prefix used by the Synology CSI driver).
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
// Audit subcommand
// ---------------------------------------------------------------------------

func cmdAudit() error {
	// Fetch K8s PVs and NAS config concurrently.
	type pvResult struct {
		pvs map[string]PV
		err error
	}
	type nasResult struct {
		cfg *NASConfig
		err error
	}

	pvCh := make(chan pvResult, 1)
	nasCh := make(chan nasResult, 1)

	go func() {
		pvs, err := getK8sPVs()
		pvCh <- pvResult{pvs, err}
	}()

	go func() {
		client, password, err := newSSHClient()
		if err != nil {
			nasCh <- nasResult{nil, err}
			return
		}
		defer client.Close()
		cfg, err := loadNASConfig(client, password)
		nasCh <- nasResult{cfg, err}
	}()

	pvRes := <-pvCh
	nasRes := <-nasCh

	if pvRes.err != nil {
		return fmt.Errorf("Kubernetes PV fetch: %w", pvRes.err)
	}
	if nasRes.err != nil {
		return fmt.Errorf("NAS config load: %w", nasRes.err)
	}

	pvs := pvRes.pvs
	cfg := nasRes.cfg

	// Categorise each LUN.
	type row struct {
		lunName string
		status  string
		pvPhase string
		sizeGiB float64
		claim   string
	}
	var rows []row

	for _, lun := range cfg.LUNs {
		pvName := pvNameFromLUN(lun.Name)
		pv, found := pvs[pvName]

		var status, pvPhase, claim string
		switch {
		case !found:
			status = "ORPHAN"
			pvPhase = "-"
		case pv.Phase == "Bound":
			status = "Bound"
			pvPhase = pv.Phase
			if pv.Namespace != "" && pv.Claim != "" {
				claim = pv.Namespace + "/" + pv.Claim
			}
		default:
			status = pv.Phase // Released, Available, …
			pvPhase = pv.Phase
		}

		rows = append(rows, row{lun.Name, status, pvPhase, lun.SizeGiB, claim})
	}

	// Sort for deterministic output.
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].status != rows[j].status {
			return rows[i].status < rows[j].status
		}
		return rows[i].lunName < rows[j].lunName
	})

	// Print report.
	counts := map[string]int{}
	fmt.Printf("%-60s  %-10s  %8s  %s\n", "LUN Name", "Status", "Size(GiB)", "Claim")
	fmt.Println(strings.Repeat("-", 100))
	for _, r := range rows {
		counts[r.status]++
		fmt.Printf("%-60s  %-10s  %8.2f  %s\n", r.lunName, r.status, r.sizeGiB, r.claim)
	}
	fmt.Println(strings.Repeat("-", 100))
	fmt.Printf("Total LUNs: %d  |  Bound: %d  |  Released: %d  |  Orphaned: %d\n",
		len(rows), counts["Bound"], counts["Released"], counts["ORPHAN"])
	return nil
}

// ---------------------------------------------------------------------------
// Cleanup subcommand
// ---------------------------------------------------------------------------

// orphan collects the information needed to delete one LUN+Target pair.
type orphan struct {
	Name string
	UUID string
	TID  string
}

// delResult carries the outcome of one LUN deletion.
type delResult struct {
	Name string
	TID  string
	Err  error
}

// deleteLUN unmaps, deletes the LUN, then deletes the Target.
// Each step opens its own session so this function is safe to call from
// multiple goroutines on the same *ssh.Client.
func deleteLUN(client *ssh.Client, password, name, uuid, tid string) error {
	steps := []struct {
		desc string
		cmd  string
	}{
		{
			"unmap LUN from target",
			fmt.Sprintf("/usr/syno/bin/synoiscsitool --unmap-lun uuid=%s", uuid),
		},
		{
			"delete LUN",
			fmt.Sprintf("/usr/syno/bin/synoiscsitool --del-lun uuid=%s", uuid),
		},
		{
			"delete target",
			fmt.Sprintf("/usr/syno/bin/synoiscsitool --del-target tid=%s", tid),
		},
	}

	for _, step := range steps {
		code, _, errOut, err := sudoRun(client, password, step.cmd)
		if err != nil || code != 0 {
			return fmt.Errorf("step %q (exit %d): %w — stderr: %s",
				step.desc, code, err, strings.TrimSpace(errOut))
		}
	}
	return nil
}

func cmdCleanup(dryRun bool, workers int) error {
	client, password, err := newSSHClient()
	if err != nil {
		return err
	}
	defer client.Close()

	// Load NAS config; fetch K8s PVs concurrently.
	type pvResult struct {
		pvs map[string]PV
		err error
	}
	pvCh := make(chan pvResult, 1)
	go func() {
		pvs, err := getK8sPVs()
		pvCh <- pvResult{pvs, err}
	}()

	cfg, err := loadNASConfig(client, password)
	if err != nil {
		return fmt.Errorf("NAS config load: %w", err)
	}

	pvRes := <-pvCh
	if pvRes.err != nil {
		return fmt.Errorf("Kubernetes PV fetch: %w", pvRes.err)
	}
	pvs := pvRes.pvs

	// Find orphaned LUNs.
	var orphans []orphan
	for _, lun := range cfg.LUNs {
		pvName := pvNameFromLUN(lun.Name)
		if _, found := pvs[pvName]; !found {
			t := cfg.byLUNName[lun.Name]
			orphans = append(orphans, orphan{
				Name: lun.Name,
				UUID: lun.UUID,
				TID:  t.TID,
			})
		}
	}
	sort.Slice(orphans, func(i, j int) bool {
		return orphans[i].Name < orphans[j].Name
	})

	if len(orphans) == 0 {
		fmt.Println("No orphaned LUNs found — nothing to do.")
		return nil
	}

	if dryRun {
		fmt.Printf("DRY RUN — would delete %d orphaned LUN(s):\n", len(orphans))
		for _, o := range orphans {
			fmt.Printf("  LUN %-50s  UUID %s  TID %s\n", o.Name, o.UUID, o.TID)
		}
		fmt.Println("\nRe-run without --dry-run to delete.")
		return nil
	}

	if workers < 1 {
		workers = 1
	}

	fmt.Printf("Deleting %d orphaned LUN(s) with %d worker(s)...\n", len(orphans), workers)

	jobs := make(chan orphan, len(orphans))
	results := make(chan delResult, len(orphans))

	var wg sync.WaitGroup
	for range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for o := range jobs {
				err := deleteLUN(client, password, o.Name, o.UUID, o.TID)
				results <- delResult{o.Name, o.TID, err}
			}
		}()
	}

	for _, o := range orphans {
		jobs <- o
	}
	close(jobs)

	// Close results channel once all workers are done.
	go func() {
		wg.Wait()
		close(results)
	}()

	var delResults []delResult
	for r := range results {
		delResults = append(delResults, r)
	}
	// Sort for deterministic output regardless of goroutine scheduling.
	sort.Slice(delResults, func(i, j int) bool {
		return delResults[i].Name < delResults[j].Name
	})

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

	// Post-deletion counts from NAS.
	_, lunCount, _, _ := sudoRun(client, password, `grep -c '\[iSCSI_LUN' /usr/syno/etc/iscsi_lun.conf`)
	_, tgtCount, _, _ := sudoRun(client, password, `grep -c '\[iSCSI_TARGET' /usr/syno/etc/iscsi_target.conf`)
	fmt.Printf("NAS now has %s LUN(s) and %s Target(s) remaining.\n",
		strings.TrimSpace(lunCount), strings.TrimSpace(tgtCount))

	return nil
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

func usage() {
	fmt.Fprintf(os.Stderr, `lun-manager -- audit and clean up orphaned Synology iSCSI LUNs

USAGE
  lun-manager <subcommand> [flags]

SUBCOMMANDS
  audit     Compare NAS iSCSI LUNs with Kubernetes PVs and print a report.
  cleanup   Delete orphaned LUNs from the NAS.
              --dry-run   Preview deletions without making any changes.
              --workers N Concurrent deletion workers (default: 1).

ENVIRONMENT VARIABLES
  SYNOLOGY_HOST      NAS hostname or IP  (required)
  SYNOLOGY_USER      SSH user            (default: admin)
  SYNOLOGY_PASSWORD  SSH / sudo password (required)
  SYNOLOGY_PORT      SSH port            (default: 22)
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
		if err := fs.Parse(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := cmdAudit(); err != nil {
			fmt.Fprintf(os.Stderr, "audit: %v\n", err)
			os.Exit(1)
		}

	case "cleanup":
		fs := flag.NewFlagSet("cleanup", flag.ExitOnError)
		dryRun := fs.Bool("dry-run", false, "Preview deletions without making any changes")
		workers := fs.Int("workers", 1, "Concurrent deletion workers")
		if err := fs.Parse(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := cmdCleanup(*dryRun, *workers); err != nil {
			fmt.Fprintf(os.Stderr, "cleanup: %v\n", err)
			os.Exit(1)
		}

	default:
		fmt.Fprintf(os.Stderr, "unknown subcommand: %q\n\n", os.Args[1])
		usage()
		os.Exit(1)
	}
}
