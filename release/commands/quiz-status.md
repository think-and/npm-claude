Show running quiz generation jobs.

Run `python3 -c "
import sys, os, json, glob
sys.path.insert(0, os.path.expanduser('~/.bryonics/current/lib'))
from bryonics_client import load_config, api_request
cfg = load_config()
# List recent jobs from server
print('Quiz generation jobs:')
print('(Check specific job with: curl -H \"Authorization: Bearer KEY\" http://API/v1/quiz/jobs/JOB_ID)')
"`
