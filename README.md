# Autonomous AI Worker

A staged Python 3.12 project for a local AI worker that plans business tasks, uses controlled tools, observes results, recovers from bounded failures, and verifies outcomes before reporting completion. The company application will use dummy invoice data only.

## Architecture

The planned LangGraph workflow passes a validated task state through these parts:

1. **Planner** turns the user's goal into a structured, executable plan.
2. **Executor** selects an allowed tool and performs one planned action.
3. **Observer** records the tool result and determines whether the action succeeded.
4. **Memory manager** persists task state, extracted values, action history, and errors.
5. **Error recovery** updates the plan, chooses a safe alternative, and enforces retry limits. Sensitive actions pause for human approval.
6. **Verifier** checks the result against the target system rather than trusting the executor's claim.
7. **Reporter** produces a concise evidence-based completion or blocked report.

Conditional LangGraph edges route successful actions toward the next step, failures through bounded recovery, and unsafe or unresolved actions to a human. The intended invoice path is: plan, search local files, extract and validate a PDF, enter it into the simulated company app using Playwright, observe the result, verify the saved record, then report evidence.

## Project structure

```text
autonomous-ai-worker/
|-- agents/                 Planner, executor, observer, recovery, verifier, reporter
|-- company_app/            Local FastAPI website and SQLite-backed invoice records
|   |-- database.py         Invoice persistence and duplicate protection
|   |-- main.py             Login, invoice routes, and workflow task API
|   `-- templates/          Server-rendered company portal pages
|-- database/               Local task and invoice databases
|-- memory/                 Task state and audit-log persistence
|   `-- memory_manager.py   SQLite-backed task records and execution events
|-- tests/                  Focused tool, app, and workflow tests
|-- tools/                  File search, PDF reading, invoice validation, browser automation
|   |-- file_search.py      Finds local PDFs; skips generated environment folders
|   |-- invoice_data.py     Validates normalized invoice values with Pydantic
|   |-- invoice_rag.py      Retrieves relevant PDF chunks with BM25 and extracts cited fields with Groq
|   |-- pdf_reader.py       Extracts PDF text with PyMuPDF
|   `-- browser_tool.py     Submits and verifies invoices with local Playwright
|-- workflow/               LangGraph state, nodes, conditional routing, and runner
|-- .env.example            Safe template for local configuration
|-- app.py                  Streamlit dashboard (later stage)
|-- config.py               Shared environment configuration
|-- groq_connection_test.py Minimal Groq API smoke test
|-- requirements.txt        Project dependencies
`-- README.md
```

This workspace is the project root. `.venv`, pip's cache, and the Playwright browser download are kept beneath it. Do not put real invoices or API keys in source control.

## Stage 1: Install

Run these commands in the VS Code PowerShell terminal from this folder. Python 3.12 must be installed.

```powershell
py -3.12 -m venv .venv
$env:PIP_CACHE_DIR = "$PWD\.pip-cache"
\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium
```

The virtual environment installs Python packages into `.venv`. The pip cache and Playwright Chromium files are also redirected to this project. Select `.venv\Scripts\python.exe` as the VS Code interpreter.

## Stage 1: Configure and test Groq

```powershell
Copy-Item .env.example .env
```

Open `.env` and replace the placeholder with your Groq API key. The file is ignored by Git; never paste the key into source code or logs. Then run:

```powershell
.\.venv\Scripts\python.exe groq_connection_test.py
```

The smoke test sends one short chat-completion request using `GROQ_MODEL`. A missing key is reported before making a network request. API availability, model access, and a valid key are required for a successful live test.

## Stage 2: Search and read invoice PDFs

The first tools stage searches a chosen local directory for `.pdf` files, extracts text from text-based PDFs, and validates invoice number, company, positive amount, and due date. Scanned/image-only PDFs are detected but not OCR-processed yet. The deterministic parser supports common labeled layouts; the workflow uses the RAG extractor below when reading invoices.

Run the focused tests with the project interpreter:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tools.py -q
```

Example use from the project root:

```python
from tools.file_search import find_pdf_files
from tools.pdf_reader import extract_invoice_data

for pdf_path in find_pdf_files("sample_invoices"):
	print(extract_invoice_data(pdf_path))
```

## Stage 3: Simulated company app

Start the app in a VS Code terminal from the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn company_app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Configure `COMPANY_APP_USERNAME` and `COMPANY_APP_PASSWORD` in the ignored project `.env`; the app has no built-in login credentials and does not display the configured values. Use dummy invoice information only. The app stores records in `database/task_history.db`, rejects duplicate invoice numbers for the same company (case-insensitively), and binds to localhost. Its session signing secret is generated at launch unless `COMPANY_APP_SESSION_SECRET` is configured.

Run the app and tools tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tools.py tests/test_company_app.py -q
```

`tools/browser_tool.py` automates local login and invoice entry with Playwright, then verifies the saved table row's invoice number, company, amount, and due date. It refuses non-localhost URLs.

Invoice workflow extraction uses `tools/invoice_rag.py`: PyMuPDF keeps page boundaries, BM25 retrieves relevant overlapping text windows, and the configured Groq model returns four structured values with page numbers and exact supporting quotes. Quotes must be present in retrieved PDF text, and the values still pass through `InvoiceData` validation before saving. This is text-based RAG; scanned image PDFs still require OCR.

## Stage 4: Agent workflow and task memory

The LangGraph state is a `TypedDict` shared by every node. The graph uses conditional edges to route a successful search to extraction, a valid extraction to the local app, and a submitted invoice to independent verification. Failures go through bounded recovery; the planner's model output is constrained to an allowlist of local invoice actions.

- `agents/planner.py` asks the configured Groq model for a Pydantic-validated plan and blocks payment or outbound-message requests before any tool runs.
- `agents/executor.py` dispatches only local PDF search, extraction, and company-app submission.
- `agents/observer.py` records whether each tool returned an acceptable result.
- `agents/recovery.py` can try the next PDF or a bounded retry. Before repeating a save, the graph checks whether the invoice already reached SQLite.
- `agents/verifier.py` compares the extracted values to the saved SQLite record and browser evidence. `agents/reporter.py` claims completion only after this check passes.
- `memory/memory_manager.py` stores task summaries and append-only events in `database/task_history.db` alongside the company invoice table.

Run a task from the project root while the company app is running and point it at a folder of text-based, labeled invoice PDFs:

```python
from workflow.graph import run_invoice_task

result = run_invoice_task(
	user_task="Find the latest invoice, enter it into the internal company app, and verify it was saved.",
	invoice_directory=r"C:\path\to\dummy-invoices",
	company_app_url="http://127.0.0.1:8000",
)
print(result["report"])
```

The workflow uses the Groq key/model from `.env` and the browser tool is restricted to localhost. Sensitive requests pause with an approval-needed status; even if approval is recorded, this stage has no payment or external-communication tools, so those actions remain blocked.

Run all local tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Stage 5: Dashboard and task API

Run the company app/API in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn company_app.main:app --host 127.0.0.1 --port 8000
```

Run the Streamlit dashboard in a second terminal:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`, enter a task and the folder containing dummy invoice PDFs, then start it. The dashboard shows streamed graph progress, the plan, completed and failed actions, retries, extracted fields, verification evidence, the final report, and saved task history.

The local FastAPI task endpoints are:

- `POST /api/tasks` runs a workflow synchronously and returns its final state.
- `GET /api/tasks?limit=50` lists recent task summaries.
- `GET /api/tasks/{task_id}` returns one task with its audit events.

Both services bind to localhost for this demo. Do not expose these endpoints or use real company data without adding production authentication and security controls.

## Development stages

1. Set up the local environment and confirm Groq connectivity.
2. Implement and test invoice file search, PDF extraction, and structured validation (implemented below).
3. Build the simulated company app and verify browser interaction with dummy records (implemented below).
4. Add the LangGraph state, planner/executor/observer/recovery/verifier/reporting nodes, and persistent audit history (implemented below).
5. Connect the Streamlit dashboard, run the complete workflow tests, and document a demo (implemented below).

Each stage will add and test its own code before moving on. The worker must never report success without verification, must stop for approval before sensitive actions, and must keep automatic retries bounded.

## Beginner notes

- `requirements.txt` lists the packages pip installs into `.venv`; version bounds avoid silently crossing major releases.
- `.env.example` documents configuration names without containing a real secret. `config.py` loads the real local `.env` and exposes the model and database settings.
- `groq_connection_test.py` defines a `main` function and a standard `__main__` entry point. It returns a process exit code, checks configuration before network access, and reports exceptions without printing the API key.
- `.gitignore` keeps virtual-environment files, local secrets, caches, generated bytecode, logs, and local database files out of Git.
- `tools/file_search.py` walks a selected folder for PDFs; `tools/pdf_reader.py` uses PyMuPDF to read text; `tools/invoice_data.py` uses Pydantic to reject missing or invalid invoice values before later workflow steps can use them.
- `tests/test_tools.py` creates temporary dummy PDFs and checks searching, extraction, parsing, and validation without requiring real invoice files or a Groq call.
- `company_app/database.py` uses SQLite transactions and a case-insensitive unique constraint to prevent duplicate vendor/invoice pairs. `company_app/main.py` uses FastAPI form handlers and signed sessions; templates render the local portal.
- `tools/browser_tool.py` selects form fields by their labels and verifies the saved record by reading the rendered table row, rather than assuming the submit action succeeded.
- `workflow/state.py` is the shared dictionary-like state; each function in `workflow/nodes.py` is a graph node that returns state updates. `workflow/graph.py` connects those nodes and uses conditional edges to choose the next node after observations and recovery.
- `memory/memory_manager.py` uses parameterized SQLite queries and JSON fields to preserve task state and an audit trail across process runs.
- `app.py` runs the local Streamlit dashboard; the `/api/tasks` FastAPI routes in `company_app/main.py` expose task start and history to local clients.