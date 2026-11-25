# Runs list lock optimization for server100

Business source commits: cea61d3 and 928845b. The two source variants preserve the existing server-specific model, cleanup, and checkpoint-resume behavior; manifest.json records exact before/after SHA256 values.

Run verify.py against each candidate before install.py. Validation is isolated from the live service and uses synthetic state and temporary pytest fixtures. install.py is single use: it replaces three verified source copies, restarts only idle 8772, and records 8872/8874 as pending activation until both evaluation and product are idle. Inspect install-intent.json and install-response.json before any recovery; do not replay install.py after an uncertain write.

Read-only preflight.py measures the correct /api/v1 endpoints and reads each explicitly mapped ATM run's committed metadata. Detailed evidence and delivery status are maintained in the product workspace audit/20261118-runs-list-nonblocking.
