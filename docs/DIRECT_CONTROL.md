# Direct control

The office can ask the robot to approve, reject, pause, resume, or change a publishing switch. The robot checks the signed request before it acts. Send `/pause` to stop publishing and work.

Before using this, add these names to the robot repository secrets:

- `CF_ACCOUNT_ID`
- `CF_KV_NAMESPACE_ID`
- `CF_API_TOKEN`
- `CONTROL_SIGNING_KEY`
