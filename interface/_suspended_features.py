"""
AIDE -- Suspended Features Archive

FinanceOS and Fit Genie are suspended from the shippable product as of
this sprint. They are not user-facing. This module is NOT imported by
main.py or interface/web.py -- it exists purely as a reference snapshot
of the wiring that was removed, so re-enabling either feature later is
a restoration job, not a re-discovery job.

To re-enable a feature:
  1. Copy the relevant block back into interface/web.py / main.py
  2. Re-add the corresponding nav links removed from YOUR_DAY_HTML
  3. Re-add the JS functions and the call site in YOUR_DAY_HTML's <script>
  4. Re-add the imports this module's docstring lists for that feature

The underlying module files (tools/finance_*.py, core/finance_*.py,
tools/fitness_os.py, web/static/js/FitGenie.jsx, web/static/js/FitnessOS.jsx,
web/templates/fit_genie.html, interface/static/finance.html) were left
untouched on disk. Only the wiring -- routes, tool registration, and
nav -- was removed from the live app.

================================================================
FINANCE -- imports that were in interface/web.py
================================================================
    from core.finance_chat import FinanceChatService
    from core.finance_engine import FinanceEngine
    from core.finance_report import FinanceReportGenerator
    from tools.finance_ingest import FinanceIngestor

================================================================
FINANCE -- Pydantic request model that was in interface/web.py
================================================================

class FinanceChatRequest(BaseModel):
    message: str
    month_key: str | None = None


================================================================
FINANCE -- page routes that were in interface/web.py
================================================================

@app.get("/finance", response_class=HTMLResponse)
@app.get("/finance/{month_key}", response_class=HTMLResponse)
async def finance_page(month_key: str | None = None):
    try:
        with open("interface/static/finance.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Finance page not found.</h1>", status_code=404)


================================================================
FINANCE -- helper that was in interface/web.py
================================================================

def _finance_db_path() -> str:
    # original body read from settings / data dir -- see git history
    # for the exact implementation prior to suspension.
    ...


================================================================
FINANCE -- API routes that were in interface/web.py
================================================================

@app.get("/api/finance/months")
async def list_finance_months():
    ...
    with sqlite3.connect(_finance_db_path()) as conn:
        ...


@app.get("/api/finance/categories")
async def get_finance_categories():
    return {"ok": True, "categories": FinanceEngine(_finance_db_path()).categories()}


@app.get("/api/finance/review")
async def get_finance_review_queue(month_key: str | None = None):
    engine = FinanceEngine(_finance_db_path())
    ...


@app.post("/api/finance/transactions/{transaction_id}/category")
async def update_finance_transaction_category(transaction_id: str, ...):
    result = FinanceEngine(_finance_db_path()).update_transaction_category(...)
    ...


@app.get("/api/finance/analysis/summary")
async def get_finance_analysis_summary(...):
    summary = FinanceEngine(_finance_db_path()).summary_for_period(...)
    ...


@app.get("/api/finance/analysis/report")
async def get_finance_analysis_report(...):
    engine = FinanceEngine(_finance_db_path())
    reporter = FinanceReportGenerator(_finance_db_path())
    ...


@app.get("/api/finance/{month_key}")
async def get_finance_month(month_key: str):
    report = FinanceReportGenerator(_finance_db_path()).monthly_report(...)
    ...


@app.post("/api/finance/upload")
async def upload_finance_statement(...):
    upload_dir = Path.home() / ".aide" / "uploads" / "finance"
    result = await FinanceIngestor(_finance_db_path()).ingest_pdf(...)
    ...


@app.get("/api/finance/chat/history")
async def get_finance_chat_history(month_key: str | None = None):
    history = FinanceChatService(_finance_db_path()).history(month_key=month_key)
    return {"ok": True, "messages": history}


@app.post("/api/finance/chat")
async def finance_chat(request: FinanceChatRequest):
    try:
        result = await FinanceChatService(_finance_db_path()).ask(
            request.message,
            month_key=request.month_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


================================================================
FINANCE -- embedded panel that was inside YOUR_DAY_HTML
================================================================
HTML (was at the position of the "Calendar Sources" section, right after it):

    <section class="section-card status-panel" id="finance-ingest-panel">
      <div class="section-head">
        <div>
          <h2>FinanceOS</h2>
          <p>Statement intake and latest monthly reports.</p>
        </div>
        <a class="nav-link" href="/finance">Open FinanceOS</a>
      </div>
      <div class="finance-upload-grid">
        <label class="finance-upload-field">
          Statement PDF
          <input id="finance-statement-file" type="file" accept="application/pdf">
        </label>
        <label class="finance-upload-field">
          Bank
          <select id="finance-bank-name">
            <option value="generic">Generic PDF</option>
            <option value="deutsche_bank">Deutsche Bank</option>
            <option value="n26">N26</option>
          </select>
        </label>
        <button class="primary" id="finance-upload-btn" onclick="uploadFinanceStatement()">Process Statement</button>
      </div>
      <div class="finance-upload-status" id="finance-upload-status">No statement processed in this session.</div>
      <div class="finance-month-list" id="finance-month-list"></div>
    </section>

JS (was in YOUR_DAY_HTML's <script> block):

    function setFinanceStatus(message, kind='info') {
      const status = document.getElementById('finance-upload-status');
      if (!status) return;
      status.textContent = message;
      status.className = `finance-upload-status ${kind === 'error' ? 'error' : kind === 'ok' ? 'ok' : ''}`.trim();
    }

    function renderFinanceMonths(months = []) {
      const list = document.getElementById('finance-month-list');
      if (!list) return;
      if (!months.length) {
        list.innerHTML = '<span class="finance-month-chip">No finance reports yet</span>';
        return;
      }
      list.innerHTML = months.slice(0, 4).map(month => `
        <a class="finance-month-chip" href="/finance/${encodeURIComponent(month.month_key)}">
          ${escapeHtml(month.month_key)} . ${escapeHtml(month.count || 0)} transactions
        </a>
      `).join('');
    }

    async function loadFinanceMonths() {
      try {
        const response = await fetch('/api/finance/months');
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.detail || 'Finance months unavailable');
        renderFinanceMonths(payload.months || []);
      } catch (error) {
        renderFinanceMonths([]);
      }
    }

    async function uploadFinanceStatement() {
      const input = document.getElementById('finance-statement-file');
      const bank = document.getElementById('finance-bank-name')?.value || 'generic';
      const button = document.getElementById('finance-upload-btn');
      const file = input?.files?.[0];
      if (!file) {
        setFinanceStatus('Choose a PDF statement first.', 'error');
        return;
      }
      const body = new FormData();
      body.append('file', file);
      const original = button?.textContent || 'Process Statement';
      if (button) {
        button.disabled = true;
        button.textContent = 'Processing...';
      }
      setFinanceStatus('Processing statement locally...');
      try {
        const response = await fetch(`/api/finance/upload?bank_name=${encodeURIComponent(bank)}`, {
          method: 'POST',
          body,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          throw new Error(payload.detail || 'Statement processing failed');
        }
        setFinanceStatus(
          `Processed ${payload.inserted_count || 0} new transactions for ${payload.month_key}. ${payload.uncategorised_count || 0} need category review.`,
          'ok'
        );
        if (input) input.value = '';
        await loadFinanceMonths();
        showToast('FinanceOS statement processed.');
      } catch (error) {
        setFinanceStatus(error.message || 'Statement processing failed.', 'error');
        showToast(error.message || 'FinanceOS upload failed.', 'error');
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = original;
        }
      }
    }

Call site that was in the page-init code: loadFinanceMonths();


================================================================
FINANCE -- nav links that were in YOUR_DAY_HTML
================================================================
    <a class="nav-link" href="/finance">FinanceOS</a>          (in hero-actions nav-pair)
    <a class="nav-link" href="/finance">Open FinanceOS</a>      (in finance-ingest-panel section-head)
Subnav note text also referenced "FinanceOS" -- restore the full original
sentence from git history rather than re-typing it.


================================================================
FITNESS / FIT GENIE -- imports that were in interface/web.py
================================================================
    from tools.fitness_os import FitnessOS   (imported lazily inside _fitness_os())


================================================================
FITNESS -- Pydantic request model that was in interface/web.py
================================================================

class FitnessOSDispatchRequest(BaseModel):
    input: str


================================================================
FITNESS -- helper that was in interface/web.py
================================================================

def _fitness_os():
    tool = _find_tool("fitness_os")
    if tool is None:
        raise HTTPException(status_code=503, detail="FitnessOS memory is not ready yet.")
    from tools.fitness_os import FitnessOS
    memory = ...  # see git history for exact memory wiring
    return FitnessOS(memory)


================================================================
FITNESS -- page route that was in interface/web.py
================================================================

@app.get("/fit-genie", response_class=HTMLResponse)
async def fit_genie_page():
    try:
        return HTMLResponse(
            content=Path("web/templates/fit_genie.html").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Fit Genie page not found.</h1>", status_code=404)


================================================================
FITNESS -- API routes that were in interface/web.py
================================================================

@app.get("/api/fitness-os/state")
async def get_fitness_os_state():
    return _fitness_os().get_state()


@app.post("/api/fitness-os/dispatch")
async def dispatch_fitness_os(req: FitnessOSDispatchRequest):
    fitness = _fitness_os()
    result = fitness.execute(req.input)
    return {"ok": True, "result": result, "state": fitness.get_state()}


@app.get("/api/fitness-os/recommendations")
async def get_fitness_os_recommendations():
    fitness = _fitness_os()
    state = fitness.get_state()
    return {"ok": True, "commands": fitness.build_vera_commands(state), "state": state}


================================================================
FITNESS -- nav link that was in YOUR_DAY_HTML
================================================================
    <a class="nav-link" href="/fit-genie">Fit Genie</a>   (in hero-actions nav-pair)


================================================================
FITNESS -- tool registration that was in main.py
================================================================
Import (top of main.py):
    from tools.fitness_os import FitnessOS   -- NOTE: original code imported
    this lazily inside a try/except, not at module top. See block below.

Registration (inside the tools list construction, after the main tools = [...] list):

    try:
        from tools.fitness_os import FitnessOS

        tools.append(FitnessOS(memory))
    except ImportError:
        logger.warning("Fit Genie is disabled in this build.")


================================================================
FINANCE -- tool registration that was in main.py
================================================================
Imports (top of main.py):
    from tools.finance_ingest import IngestBankStatementTool
    from tools.finance_budget import (
        CategoriseTransactionTool,
        CreateFinanceGoalTool,
        SetBudgetTargetTool,
        SetupFinanceBudgetTool,
    )
    from tools.finance_report import GetMonthlyOverviewTool, GetProjectFinanceTool

Registration (inside the main tools = [...] list, in this order):
        IngestBankStatementTool(),
        GetMonthlyOverviewTool(),
        SetBudgetTargetTool(),
        SetupFinanceBudgetTool(),
        CategoriseTransactionTool(),
        CreateFinanceGoalTool(),
        GetProjectFinanceTool(),
"""
