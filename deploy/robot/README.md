# RAAS robot agent — install on the Booster K1

The agent (`robot_agent.py`, stdlib only, runs on the system Python 3.10 next to ROS) polls the
RAAS server, drops each active renter's input files into `/mnt/raas/<booking-id>/in/`, and when
the server asks for a purge it uploads whatever the operator left in `/mnt/raas/<booking-id>/out/`,
deletes the booking's directory, and confirms. `/mnt/raas` is a tmpfs: renter data lives in RAM only.

## The rule

**The agent never executes renter files. The operator reviews and runs them by hand.**
`/mnt/raas` is mounted `noexec` on purpose: read `BRIEF.txt`, inspect `in/`, and run things
explicitly (e.g. `python3 /mnt/raas/<id>/in/script.py`) only after reading them. Anything a renter
should get back goes into `out/`; it is uploaded and shredded with the rest when the session ends.

## Install (from your workstation)

```sh
# 1. copy the agent and the units
ssh booster@<robot> 'mkdir -p ~/raas'
scp robot_agent.py booster@<robot>:~/raas/
scp deploy/robot/mnt-raas.mount deploy/robot/sxth-raas-agent.service booster@<robot>:/tmp/

# 2. on the robot: tmpfs mount + agent config
ssh booster@<robot>
sudo install -m 644 /tmp/mnt-raas.mount /tmp/sxth-raas-agent.service /etc/systemd/system/
sudo mkdir -p /mnt/raas
cat > ~/raas/agent.env <<'EOF'
RAAS_API_URL=https://raas-api.sixthsenselabs.xyz
ROBOT_TOKEN=<the ROBOT_TOKEN configured on the server>
RAAS_DIR=/mnt/raas
RAAS_POLL_S=5
EOF
chmod 600 ~/raas/agent.env

# 3. enable
sudo systemctl daemon-reload
sudo systemctl enable --now mnt-raas.mount
sudo systemctl enable --now sxth-raas-agent.service

# 4. check
findmnt /mnt/raas                      # tmpfs, 2G, noexec
systemctl status sxth-raas-agent
journalctl -u sxth-raas-agent -f       # one line per action, never file contents
```

## Notes

- The agent refuses to start unless `RAAS_API_URL` (https) and `ROBOT_TOKEN` are set and
  `RAAS_DIR` is a mount point; the unit also `Requires=mnt-raas.mount`. No tmpfs, no agent.
- Downloads skip if they would leave less than 256 MB free on `/mnt/raas`; raise `size=` in
  `mnt-raas.mount` if renters legitimately need more.
- **Put results in `out/` before you mark the session complete.** Within one poll of "complete" the
  agent uploads `out/`, deletes `/mnt/raas/<id>/` and confirms; anything written later is gone (and
  a shell sitting in that directory is left in a deleted cwd).
- If an upload from `out/` fails, nothing is deleted and the next poll retries; already-uploaded
  files are remembered in `<id>/.uploaded` so nothing is sent twice.
- A purge that never finishes (the log repeats `upload ... failed: HTTP 409` or `HTTP 413` every
  poll) means the server will not take those results: the booking was already shredded server-side,
  or the results exceed the booking's upload cap. Copy what you need out of `/mnt/raas/<id>/out/`,
  hand it to the renter another way, then `rm -rf /mnt/raas/<id>`; the next poll confirms the purge.
- A reboot empties `/mnt/raas`. Active bookings are re-downloaded on the next poll; anything in
  `out/` that was not yet uploaded is lost.
- Local test without the robot: `python3 robot_agent.py --selftest` (prints `ROBOT-AGENT-OK`), or
  `RAAS_API_URL=... ROBOT_TOKEN=... RAAS_DIR=/tmp/raas python3 robot_agent.py --allow-disk --once`.
