Cancel a running quiz generation job.

Run `python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~/.bryonics/current/lib'))
from bryonics_client import load_config, api_request
cfg = load_config()
job_id = '$ARGUMENTS'.strip()
if not job_id:
    print('Usage: /quiz-cancel <job_id>')
else:
    result = api_request(cfg, 'POST', f'/v1/quiz/jobs/{job_id}/cancel', timeout=5.0)
    if result:
        print(f'Job {job_id}: {result.get(\"status\", \"unknown\")}')
    else:
        print('Error: could not cancel job')
"`
