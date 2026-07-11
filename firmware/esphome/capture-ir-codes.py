#!/usr/bin/env python3
"""
Interactive IR code capture for the ESP32-C6 IR blaster (lab project 03-019).

Streams `esphome logs`, and for every NEW button you press it prompts you for a
name, then appends it to a YAML file you can hand back to Claude to build the
blaster.yaml transmit config.

  python3 capture-ir-codes.py                              # OTA (ir-blaster.local)
  python3 capture-ir-codes.py --device /dev/cu.usbmodem1101  # USB (most reliable)
  python3 capture-ir-codes.py --yaml X --out Y --device D

Flow: press a button -> get prompted -> type a name  (blank or 's' = skip, 'q' = quit).
Repeat frames from a held button, and codes you've already named, are ignored.
Run it from ~/src/homelab/firmware/esphome (so it finds the yaml + secrets.yaml).
"""
import argparse, os, queue, re, subprocess, threading, time

ANSI = re.compile(r"\x1b\[[0-9;]*m")
RECV = re.compile(r"Received\s+([A-Za-z0-9]+):\s*(.+?)\s*$")


def parse(line):
    m = RECV.search(ANSI.sub("", line))
    if not m:
        return None
    # drop the repeat counter so a held vs tapped press dedups to one button
    rest = re.sub(r"\s*command_repeats=\d+", "", m.group(2)).strip()
    return (m.group(1), rest)


def reader(proc, q):
    for raw in iter(proc.stdout.readline, ""):
        p = parse(raw)
        if p:
            q.put(p)
    q.put(None)  # EOF sentinel


def drain(q, secs):
    """Swallow queued frames for `secs` — collapses the burst of repeat frames from one press."""
    end = time.time() + secs
    while time.time() < end:
        try:
            if q.get(timeout=max(0, end - time.time())) is None:
                return True  # hit EOF
        except queue.Empty:
            return False
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default="ir-blaster-learn.yaml")
    ap.add_argument("--out", default="ir-codes.yaml")
    ap.add_argument("--device", default="ir-blaster.local",
                    help="esphome --device: OTA host (ir-blaster.local) or USB port (/dev/cu.usbmodem1101)")
    a = ap.parse_args()

    fresh = (not os.path.exists(a.out)) or os.path.getsize(a.out) == 0
    out = open(a.out, "a")
    if fresh:
        out.write("# IR codes captured from remotes — hand this to Claude to build blaster.yaml\n")
        out.flush()

    print(f"→ launching:  esphome logs {a.yaml}")
    print("  give it ~10s to connect, then point a remote and press buttons.\n")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        ["esphome", "logs", a.yaml, "--device", a.device],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
    )
    q = queue.Queue()
    threading.Thread(target=reader, args=(proc, q), daemon=True).start()

    seen, n = {}, 0
    try:
        while True:
            item = q.get()
            if item is None:
                print("\n(log stream ended — is the device online?)")
                break
            proto, rest = item
            sig = f"{proto}|{rest}"
            drain(q, 0.6)  # collapse the repeat-frame burst from this one press
            if sig in seen:
                continue  # already named this button
            print(f"\n\U0001F4E1  {proto}: {rest}")
            try:
                name = input("    name it (blank/s = skip, q = quit): ").strip()
            except EOFError:
                break
            drain(q, 0.1)  # toss any frames that arrived while you were typing
            if name.lower() == "q":
                break
            if name == "" or name.lower() == "s":
                print("    …skipped")
                continue
            seen[sig] = name
            n += 1
            out.write(f'- name: "{name}"\n  decode: "{proto}: {rest}"\n')
            out.flush()
            print(f"    ✓ saved #{n}  →  {a.out}")
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        out.close()
        print(f"\nDone — {n} code(s) in {a.out}. Paste that file back to build blaster.yaml.")


if __name__ == "__main__":
    main()
