# Agents & Workflow Nodes

This document defines the specialized agents (nodes) that constitute the LinkedIn Job Application automation system. The workflow is orchestrated using **LangGraph**, where each agent is a node in the state graph.

## Workflow Overview

**Flow:** `Fetch Jobs` → `Filter Job` → `Compose CV` → `Generate PDF` → `Human Review` → `Apply on LinkedIn`

Each agent operates on a shared state (`WorkflowState`) and passes data to the next stage.

---

## Reference Implementations

The `Obsolete/` directory contains **two production-ready projects** that serve as valuable reference implementations:

### 1. **Auto_job_applier_linkedIn** (GodsScion)
- **Status:** Production-ready, actively maintained
- **Architecture:** Selenium-based web automation with AI integration
- **Key Features:**
  - Web scraping with undetected-chromedriver (stealth mode)
  - Multi-LLM support (OpenAI, DeepSeek, Gemini)
  - Intelligent form filling with AI-powered question answering
  - Application history tracking (CSV + Flask web UI)
  - Comprehensive configuration system (5 config files)
  - Robust error handling and logging
- **Useful Components:**
  - `modules/clickers_and_finders.py` - Reusable Selenium utilities
  - `modules/ai/` - Multi-provider AI integration patterns
  - `modules/validator.py` - Configuration validation framework
  - `app.py` - Flask-based application history viewer
- **Documentation:** See `Obsolete/Auto_job_applier_linkedIn/ARCHITECTURE.md` for detailed analysis

### 2. **Jobs_Applier_AI_Agent_AIHawk** (AIHawk)
- **Status:** Production-ready, featured in major media (Business Insider, TechCrunch, The Verge, Wired)
- **Architecture:** LangChain-based with FAISS vector search
- **Key Features:**
  - Semantic job parsing using vector embeddings
  - LLM-powered resume tailoring (section-by-section generation)
  - Professional PDF generation via Chrome DevTools Protocol
  - Multi-LLM support (OpenAI, Claude, Gemini, HuggingFace, Ollama, Perplexity)
  - Pydantic-based type-safe data models
  - Customizable resume styling
- **Useful Components:**
  - `src/llm_manager.py` - Factory pattern for multi-LLM support
  - `src/resume_facade.py` - Facade pattern for resume generation
  - `src/llm_job_parser.py` - Semantic job description extraction
  - `src/utils/chrome_utils.py` - CDP-based PDF generation
  - `resume_schemas/` - Pydantic models for type safety
- **Documentation:** See `Obsolete/Jobs_Applier_AI_Agent_AIHawk/ARCHITECTURE.md` for comprehensive analysis

### How to Use These References

**For CV Composer Agent:**
- Study AIHawk's section-by-section CV generation approach
- Review their prompt engineering strategies
- Examine their Pydantic schemas for CV data validation

**For Browser Automation:**
- Reference Auto_job_applier's Selenium utilities
- Learn from their stealth mode implementation
- Study their form-filling logic and question answering

**For LLM Integration:**
- Both projects demonstrate multi-provider support patterns
- AIHawk shows advanced LangChain usage
- Auto_job_applier shows simpler direct API integration

**For PDF Generation:**
- AIHawk: Chrome DevTools Protocol approach (high-fidelity)
- Our implementation: WeasyPrint + Jinja2 (simpler, template-driven)

**Note:** While these projects are production-ready, our implementation uses a different architecture (LangGraph workflow) and different design decisions (e.g., WeasyPrint over CDP for PDF generation). Use them as inspiration and reference, not as direct copy sources.

---

## 1. Job Fetcher Agent
- **Node Name:** `fetch_jobs`
- **Type:** Extraction / Scraper
- **Responsibility:** Periodically queries LinkedIn for new job postings matching defined criteria.
- **Inputs:**
  - Search parameters (keywords, location, remote preference)
  - Execution schedule (hourly)
- **Outputs:**
  - Raw job posting data
- **State Updates:**
  - `job_posting`: Dict containing title, company, description, URL, etc.
- **Tools/Services:**
  - Web Scraper (fallback)

## 2. Job Filter Agent
- **Node Name:** `filter_job`
- **Type:** LLM (Evaluator)
- **Responsibility:** Analyzes job descriptions to screen out irrelevant positions or those containing "hidden" disqualifiers (e.g., citizenship reqs, fake remote).
- **Inputs:**
  - `job_posting`
  - `filters` (User defined criteria)
- **Outputs:**
  - Suitability decision
- **State Updates:**
  - `is_suitable`: `True` or `False`
- **Routing:**
  - If `True` → Proceed to **Compose CV**
  - If `False` → **END** workflow
- **Model Recommendation:** Fast/Cheaper LLM (e.g., DeepSeek, Grok)

## 3. CV Composer Agent
- **Node Name:** `compose_cv`
- **Type:** LLM (Content Generator)
- **Responsibility:** Tailors the user's Master CV to specifically target the job requirements.
- **Inputs:**
  - `master_cv` (Full career history)
  - `job_posting`
  - `user_feedback` (If in retry loop)
- **Outputs:**
  - Structured CV data
- **State Updates:**
  - `tailored_cv_json`: JSON object matching the CV schema
- **Key Actions:**
  - Summarize job requirements.
  - Reorder and emphasize Experience items.
  - Rewrite Professional Summary.
  - Filter/Highlight Skills.
  - **Strict JSON Schema validation** to ensure data integrity.
- **Model Recommendation:** High-Intelligence LLM (e.g., GPT-4o, Claude 3.5 Sonnet)

## 4. PDF Generator Agent
- **Node Name:** `generate_pdf`
- **Type:** Deterministic Service
- **Responsibility:** Renders the tailored CV JSON into a professional PDF file.
- **Inputs:**
  - `tailored_cv_json`
  - template selection
- **Outputs:**
  - PDF File
- **State Updates:**
  - `tailored_cv_pdf_path`: Absolute path to the generated PDF.
- **Tools/Services:**
  - `PDFGenerator` class
  - WeasyPrint (HTML -> PDF engine)
  - Jinja2 (Templating)

## 5. Human Review Agent (HITL)
- **Node Name:** `human_review`
- **Type:** Human-in-the-Loop (Approval)
- **Responsibility:** Pauses execution to present the job and tailored CV to the user for final verification.
- **Inputs:**
  - `job_posting`
  - `tailored_cv_pdf_path`
- **Outputs:**
  - User Decision
- **State Updates:**
  - `user_approval`: `approved`, `declined`, or `retry`
  - `user_feedback`: Optional notes for the agent.
- **Routing:**
  - `approved` → **Apply on LinkedIn**
  - `declined` → **END**
  - `retry` → Back to **Compose CV** (with feedback)
- **Interface:** Tinder-like UI or Web Dashboard.

## 6. LinkedIn Application Agent
- **Node Name:** `apply_linkedin`
- **Type:** Browser Automation
- **Responsibility:** Navigates the LinkedIn "Easy Apply" flow to submit the application.
- **Inputs:**
  - `job_posting` (URL)
  - `tailored_cv_pdf_path`
  - User Credentials
- **Outputs:**
  - Submission Result
- **State Updates:**
  - `application_status`: `success` or `failure`
- **Tools/Services:**
  - Playwright (Headless Browser)
  - Handling of form fields, resume upload, and review steps.

## 7. Notification Agent
- **Node Name:** `send_notification`
- **Type:** Utility
- **Responsibility:** Handles error reporting and failure alerts.
- **Inputs:**
  - `error_message`
  - `application_status`
- **Outputs:**
  - Alert sent
- **State Updates:** None (Side effect only)
- **Tools/Services:**
  - Webhook / Email / Slack / Telegram

---

## Shared Data Structure (WorkflowState)

```python
class WorkflowState(TypedDict):
    job_posting: dict          # The job being processed
    filters: dict             # User preferences
    master_cv: dict           # Source of truth for CV data
    is_suitable: bool         # Result of Filter Agent
    tailored_cv_json: dict    # Result of CV Composer
    tailored_cv_pdf_path: str # Result of PDF Generator
    user_approval: str        # 'approved', 'declined', 'retry'
    user_feedback: str        # Feedback for retry
    application_status: str   # 'success', 'failure'
    error_message: str        # Details on any failure
```
