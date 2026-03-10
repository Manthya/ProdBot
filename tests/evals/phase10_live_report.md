# Phase 10.0 Live Orchestrator Eval Report

WS URL: ws://localhost:8000/api/chat/stream
Model: qwen2.5:14b-instruct
Provider: ollama
Total: 51/98 passed

## Tag Breakdown
deterministic  | ######################........ | 9/12
domain:fetch   | ##########.................... | 4/12
domain:filesystem | .............................. | 0/11
domain:git     | ###........................... | 1/10
domain:none    | #######################....... | 23/30
domain:sqlite  | ##########.................... | 1/3
domain:time    | ######################........ | 9/12
multi_step     | ###################........... | 13/20
network        | ##########.................... | 4/12
phase:complex  | ###################........... | 13/20
phase:general  | #######################....... | 23/30
phase:medium   | #########..................... | 15/48

## Failures
- G-021: Explain Docker in one paragraph -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- G-022: What is concurrency? -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- G-023: What is OAuth? -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- G-024: What is load balancing? -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- G-025: Explain hashing -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- G-026: Explain logging levels -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- G-027: What is latency? -> tools=[] errors=[{'error': 'no close frame received or sent', 'type': 'ConnectionClosedError', 'repr': 'ConnectionClosedError(None, None, None)'}]
- T-032: Current time in Tokyo -> tools=['convert_time'] errors=[]
- T-038: timezone for PST? -> tools=['convert_time'] errors=[]
- T-040: current time UTC -> tools=[] errors=[]
- GIT-043: git status -> tools=[] errors=[]
- GIT-044: Show me git log -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- GIT-045: git branch -> tools=['git_branch'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- GIT-046: git diff -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- GIT-048: show git status -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- GIT-049: git commit history -> tools=[] errors=[]
- GIT-050: git status for this repo -> tools=[] errors=[]
- GIT-051: show git branches -> tools=['git_branch'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- GIT-052: git diff last commit -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- F-053: Open https://example.com -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- F-056: Search the web for cats -> tools=[] errors=[]
- F-057: Look up the Eiffel Tower online -> tools=['web_search_duckduckgo'] errors=[]
- F-058: Find online info about pandas -> tools=['web_search_duckduckgo'] errors=[]
- F-060: Download https://example.com/file -> tools=['fetch_json'] errors=[]
- F-061: Search the web for latest Python version -> tools=['web_search_duckduckgo'] errors=[]
- F-063: Open https://example.com and summarize -> tools=['puppeteer_navigate'] errors=[]
- F-064: search the web for climate data -> tools=['web_search_duckduckgo'] errors=[]
- FS-065: List files in this folder -> tools=[] errors=[]
- FS-066: Read ./README.md -> tools=['read_text_file'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- FS-067: Open docs/phase10.0.md -> tools=['read_text_file'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- FS-068: Show package.json -> tools=['read_text_file'] errors=[]
- FS-069: List directory -> tools=[] errors=[]
- FS-075: Read file /etc/hosts -> tools=['read_text_file'] errors=[]
- FS-076: List files here -> tools=['list_directory'] errors=[]
- FS-077: Show me the directory tree -> tools=[] errors=[]
- FS-078: Open ./pyproject.toml -> tools=['read_text_file'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- FS-079: Read tests/evals/phase10_cases.csv -> tools=['read_text_file'] errors=[]
- FS-080: Show contents of docs/phase10.0_testing.md -> tools=['read_text_file'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- SQL-081: select * from users -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- SQL-087: select count(*) from users -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- C-098: Read docs/phase10.0.md and extract key changes -> tools=['read_text_file'] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- C-105: Fetch URL and extract title -> tools=[] errors=[]
- C-106: Search for API docs then summarize -> tools=[] errors=[{'error': '', 'type': 'TimeoutError', 'repr': 'TimeoutError()'}]
- C-108: Check git status and then show diff -> tools=[] errors=[]
- C-110: Search online then update local notes -> tools=[] errors=[]
- C-111: Read error log and find root cause -> tools=[] errors=[]
- C-112: Analyze code and suggest fixes -> tools=[] errors=[]