# AI-Powered Lead Generation & Outreach Platform

An AI-powered platform that automates business lead discovery, Ideal Customer Profile (ICP)-based qualification, prospect research, contact enrichment, and personalized email outreach.

## Overview

Identifying and qualifying potential customers manually can be time-consuming. This project streamlines the lead generation process by combining location-based business discovery, AI-powered research, contact enrichment, and email outreach in a single workflow.

Users can define their target audience, search criteria, and business requirements to discover relevant prospects, evaluate their suitability, identify professional contacts, and prepare personalized outreach.

## Key Features

- **Configurable Lead Discovery:** Discover potential businesses using search terms, geographic location, radius, and result limits.
- **ICP-Based Qualification:** Evaluate potential leads against an Ideal Customer Profile and specified business requirements.
- **AI-Powered Prospect Research:** Use an AI agent to research businesses, assess relevance, and provide qualification scores with supporting explanations and source links.
- **Automated Data Filtering:** Filter duplicate, incomplete, closed, or out-of-scope business listings before further processing.
- **Contact Enrichment:** Integrate TREG to identify relevant decision-makers, discover professional email addresses, and verify available addresses.
- **Personalized Email Outreach:** Prepare tailored email drafts and support configured email delivery workflows.
- **Cost-Aware Enrichment:** Apply per-request cost limits and run-level budgets to help manage contact-enrichment expenses.
- **Interactive Web Interface:** Use a Flask-based application to interact with the lead generation workflow.
- **Mock Mode:** Test supported interface and discovery workflows with sample data without requiring live Google Places or Hermes research.

## How It Works

1. **Define the target audience:** Specify search parameters, target location, keywords, and ICP requirements.
2. **Discover potential leads:** Retrieve business listings through the Google Places API.
3. **Validate and filter results:** Remove duplicates, incomplete records, closed businesses, and listings outside the specified criteria.
4. **Research and qualify prospects:** Use Hermes to research relevant businesses, evaluate their fit, and score qualified prospects.
5. **Enrich contact information:** Use TREG to search for decision-makers and discover or verify professional email addresses.
6. **Prepare personalized outreach:** Generate relevant email drafts using prospect information and configured outreach settings.
7. **Send or review emails:** Use the configured email workflow to send messages or open drafts for review, depending on the selected mail configuration.

## Architecture

The application follows a modular pipeline:

**Lead Discovery → Data Validation → AI Research → ICP Qualification → Contact Enrichment → Email Outreach**

The major components have distinct responsibilities:

- **Flask application:** Provides the web interface and coordinates the workflow.
- **Google Places API:** Supplies business discovery data.
- **Hermes:** Performs AI-assisted prospect research.
- **TREG API:** Supports contact discovery and email enrichment and verification.
- **Email module:** Prepares and delivers messages through supported email configurations.
- **Frontend:** Uses HTML, CSS, and JavaScript to present the application interface and interact with backend endpoints.

## Technology Stack

| Component | Technologies |
|---|---|
| Programming Language | Python |
| Backend Framework | Flask |
| AI Agent | Hermes CLI |
| Business Discovery | Google Places API |
| Contact Enrichment | TREG API |
| Email Outreach | SMTP / Gmail integration |
| Frontend | HTML, CSS, JavaScript |
| Data Exchange | JSON |
| Version Control | Git, GitHub |

## Getting Started

### Prerequisites

- Python 3.10 or later
- Git
- Hermes CLI installed and configured
- A Google Places API key for live business discovery
- A TREG token for contact enrichment
- Email configuration if SMTP-based sending is required

### 1. Clone the Repository

```bash
git clone https://github.com/SaanviSBhandiwad/Lead-Generation.git
cd Lead-Generation
```

### 2. Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, activate the environment using:

```powershell
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Credentials

Configure API keys and other credentials through environment variables or your local Hermes environment configuration, as supported by the application.

The application may use the following settings:

| Variable | Purpose |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Google Places API authentication |
| `TREG_TOKEN` | TREG API authentication |
| `TREG_RUN_BUDGET` | Maximum enrichment budget per run |
| `TREG_BASE` | TREG API base URL |
| `HERMES_TIMEOUT_SECONDS` | Timeout for Hermes research requests |
| `PROMPTIQ_SKILL` | Optional path to the configured Hermes skill |
| `MOCK` | Enables supported mock-mode functionality |
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP server port |
| `SMTP_PASSWORD` | SMTP password or app password, where applicable |

Additional email settings may be required depending on the mail configuration. Check the source code for the exact supported variable names.

**Security:** Never commit API keys, access tokens, passwords, private prospect data, or email logs to the repository. Keep credentials in a local configuration file outside the project or use environment variables.

### 5. Run the Application

Start the Flask application:

```bash
python3 app.py
```

Open the local application in your browser at:

`http://127.0.0.1:5050`

The default port is `5050`; the application can also use the `PORT` environment variable.

### 6. Run in Mock Mode

To test supported sample-data functionality without live Google Places or Hermes research, run:

```bash
MOCK=1 python3 app.py
```

Mock mode does not necessarily disable every external integration. Check the implementation before using it for testing email or contact-enrichment functionality.

## Security and Responsible Outreach

- Keep credentials and tokens outside version control.
- Avoid committing personal, confidential, or unnecessary prospect data.
- Use enrichment budgets and per-request cost limits.
- Review AI-generated research and qualification results before relying on them.
- Review personalized emails before sending them.
- Follow applicable privacy, data-protection, and anti-spam requirements.
- Use only authorized API credentials and respect third-party service terms.

## Future Improvements

- Integrate additional business discovery sources.
- Improve ICP scoring and qualification explanations.
- Add persistent storage and lead history.
- Introduce analytics for lead quality and outreach performance.
- Integrate with CRM platforms.
- Add configurable qualification rules and outreach templates.
- Improve automated testing and deployment workflows.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
