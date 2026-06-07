# Operator Reset Checklist (Between Demos)

**Time required:** ~30 seconds

---

## Quick Reset

1. **Slack:** Switch to next pre-created channel
   - Channels available: `#demo-run-1` through `#demo-run-10`
   - Current run: _______ (pencil mark)

2. **Terminal:** Reset to scenario start
   ```bash
   cd ~/demo && clear
   cat mock-telemetry/combined_timeline.json | jq '.timeline[0]' 
   # Shows T+0 (healthy baseline)
   ```

3. **Browser tabs:** Ensure these are ready
   - Tab 1: Slack channel (new one)
   - Tab 2: Architecture diagram (Screen A content)
   - Tab 3: Mock CloudWatch dashboard (static screenshot)

4. **Skill spec file:** Close and reopen in editor (fresh buffer)

---

## Extended Reset (if demo went off-script)

5. Close all extra terminal windows
6. Clear browser history / close extra tabs
7. Verify Screen A is showing slide deck (auto-loop)
8. Take a breath, smile at next attendee

---

## Demo Entry Points (flexible)

| If time is... | Start at... | Skip to... |
|---------------|-------------|------------|
| 8 minutes | Act 1 (full cascade) | — |
| 5 minutes | Act 1 + Act 4 (summary) | Skip Act 2 & 3 |
| 3 minutes | Act 4 only (show resolution + comparison) | — |
| 1 minute | "Without vs With skills" comparison only | — |

---

## Emergency Escape Hatches

- **If agent/Slack is slow:** Switch to pre-rendered expected-outputs/*.md
- **If question derails:** "Great question — let me show you the full cascade first, then we'll address that"
- **If attendee wants to try:** Hand them QR code card, say "sign up here, we'll get you set up"
- **If asked about pricing:** "Currently in preview — talk to your account team"
