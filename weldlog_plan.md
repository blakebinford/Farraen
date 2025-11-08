# Farraen Weld Traceability Expansion Plan

## Current Capabilities Snapshot
- The `documents` app already stores organization-scoped files by type (e.g., MTR, WPS, NDE) with versioning and SHA-256 support, providing a basic traceable document log.
- The `drive` app adds hierarchical folders, file version history, check-in/out, audit events, and per-project navigation—essentially a lightweight Google Drive inside each organization/project.
- Organizations, memberships, and project roles are modeled with role-based access control, which we can reuse for weld-log permissions and QA sign-offs.

## Target Vision
Evolve Farraen from document control into a weld traceability and compliance platform that:
1. Tracks each weld (who, what, where, when) and links it to required documentation (MTRs/heat numbers, WPS, NDE reports, welder qualifications).
2. Supports compliance workflows (reviews, sign-offs, NCRs, repairs).
3. Produces weld map PDFs by annotating design drawings with weld IDs, heat numbers, and NDE references for field teams.

## Feature Themes & Requirement Clarifications

### 1. Weld Traceability Ledger
- **Entities**: weld joints, materials/spools, heat numbers, weld passes, inspectors, welders, equipment, consumables.
- **Core actions**: create/update weld entry, attach documents, track status (planned, in-progress, complete, repaired).
- **Data capture**: weld procedure used, welder qualification, NDE schedule/results, fit-up/preheat, environmental conditions.
- **Clarifications requested**
  - What level of granularity is required (joint-level, pass-level, repairs, tie-ins)?
  - Do weld IDs come from field systems (barcode/QR) or will Farraen generate them?
  - Required compliance standards (ASME, API 1104, CSA Z662, company-specific)?

### 2. Document & Compliance Linkage
- Use existing `Document` records and Drive storage as authoritative sources.
- Requirements: enforce that each weld references the correct MTRs/WPS/NDE results; flag gaps.
- **Clarifications requested**
  - Do we need automatic document approval workflows or is manual attachment sufficient initially?
  - Should MTR/WPS approval validity (expiry, revocation) be tracked and validated automatically?

### 3. Personnel & Qualification Management
- Store welder qualifications, continuity logs, inspector certifications.
- Tie qualifications to weld entries; block assignments if qualifications expire.
- **Clarifications requested**
  - Should welder continuity be tracked automatically based on weld activity, or imported from HR/LMS?
  - Do inspectors need separate sign-off flows or can organization admins handle reviews?

### 4. Quality Assurance & Reporting
- NDE scheduling, results, acceptance criteria, repair loops.
- Generate quality dossiers (per project, spread, hydrotest package).
- **Clarifications requested**
  - Preferred NDE types (RT, UT, MT, PT) and metrics to capture?
  - Required outputs: PDF packages, dashboards, regulatory exports?

### 5. Weld Map PDF Production
- Produce annotated PDF drawings that clearly identify weld numbers, associated heat numbers, and NDE references at their physical locations on the drawing.
- Maintain associations between weld entries and specific drawing sheets, callouts, or viewport references to enable accurate annotation.
- **Clarifications requested**
  - Source of base drawings (e.g., CAD exports, scanned plans) and desired annotation tooling.
  - Expected level of automation for placing annotations versus manual review/placement workflows.

## Data Dictionary & Form Layouts (Draft)

### Core Entities
| Entity | Key Fields | Relationships | Form Layout Notes |
| --- | --- | --- | --- |
| **Project** | Name, code, description | Organization ↔ Project | Reuse existing project creation form; add toggle to enable weld logging features. |
| **Drawing Sheet** | Project, title, number, revision, file (Document link), sheet metadata | Project 1:N Drawing Sheet; Drawing Sheet 1:N Weld Annotation | Form: select project, upload/attach drawing (reuse Drive picker), enter sheet number/title, revision date. |
| **Material Piece** | Project, identifier, component type, material spec, size, heat number(s) | Material Piece N:M Heat Number; Material Piece 1:N Weld Joint (as root/branch) | Form: search existing materials, enter details, attach MTR documents. |
| **Heat Number** | Identifier, mill, material grade, MTR document reference, received date | Heat Number 1:N Material Piece | Form includes document picker for MTR, date fields, remarks. |
| **Weld Joint** | Project, unique weld ID, joint type, drawing reference, station/chainage text, location notes | Weld Joint 1:N Weld Pass, 1:1 Weld Summary, N:M Documents, N:M Material Piece | Form: sections for identification (ID, type, drawing), materials (base materials, consumables), status. |
| **Weld Pass** | Weld Joint, pass number, process, date/time, welder(s), amperage/voltage, travel speed, preheat/interpass temps, consumable batch | Weld Pass N:M Welder, N:M Consumable Batch | Form uses repeatable field group per pass, inline welder selection. |
| **Welder** | Person reference, qualifications, continuity status, cert documents | Welder 1:N Qualification, Welder N:M Weld Pass | Form: select organization member, list qualifications with expiry, upload continuity log. |
| **Qualification** | Welder, qualification type, procedure, expiry, issuing authority, documents | Qualification 1:1 Document, Qualification N:M WPS | Form: capture issuing info, attach documentation, set reminders. |
| **WPS (Weld Procedure Specification)** | Identifier, revision, process, thickness range, position, documents | WPS 1:N Weld Joint (usage), WPS 1:N Qualification | Form: attach documents, define validity (dates, thickness, position). |
| **NDE Request** | Weld Joint, method, scheduled date, technician, acceptance criteria | NDE Request 1:1 NDE Result, NDE Request N:M Documents | Form: schedule details, method dropdown, acceptance notes. |
| **NDE Result** | Linked request, performed date, outcome (accept/reject), indications, technician, report document | 1:1 with NDE Request | Form: auto-populated from request, capture results, attach report, mark for repair if needed. |
| **Repair Record** | Weld Joint, repair number, reason, corrective actions, additional passes, documents | Repair Record 1:N Weld Pass (repair), Repair Record N:M Documents | Form: triggered on NDE rejection, capture defect details, approvals. |
| **Weld Annotation** | Drawing Sheet, Weld Joint, annotation coordinates/geometry, label text, heat/NDE references | Links to generated PDF overlay | Form: select weld, specify annotation location (manual coordinate entry or interactive tool), preview overlay. |

### Form Layout Principles
- **Tabbed forms** for complex entries (e.g., Weld Joint tabs: Overview, Materials, Passes, Documents, QA).
- **Inline document picker** leveraging Drive modal for attaching controlled documents.
- **Validation prompts** before saving (e.g., warn if welder qualification expired, missing MTR).
- **Bulk import templates** for weld joints and NDE results with CSV upload and validation preview.
- **Audit trail sidebar** showing recent changes, approvals, and outstanding tasks per record.

## Phased Timeline with Acceptance Criteria

### Phase 1: Weld Ledger MVP (6–8 weeks)
- **Deliverables**
  - CRUD for weld joints, materials, heat numbers, welders, qualifications.
  - Ability to attach documents (MTR, WPS, NDE report placeholders) to weld entries.
  - Validation to ensure each weld has an assigned WPS and qualified welder.
  - Export of weld register (CSV/XLSX) with key attributes.
- **Acceptance Criteria**
  - Users can create and edit weld records with linked materials and documents without database access.
  - System prevents saving welds if associated welder qualifications are expired.
  - Weld register export matches on-screen data for selected project.
  - Audit trail captures creation and modification events for weld records.

### Phase 2: QA Workflow & NDE Tracking (8–10 weeks)
- **Deliverables**
  - NDE request/result workflow with scheduling, technician assignment, outcome tracking.
  - Electronic sign-offs for weld completion and repair approvals with role-based permissions.
  - Notifications or dashboard alerts for overdue NDE or expiring qualifications.
  - Bulk import for weld activity and NDE results.
- **Acceptance Criteria**
  - QA managers can assign NDE inspections and receive notifications on overdue items.
  - Rejected welds automatically create repair tasks requiring resolution before closure.
  - Electronic sign-off history is visible per weld with timestamps and responsible roles.
  - Bulk import validates data and reports errors without partial saves.

### Phase 3: Annotated Weld Map PDFs (6 weeks)
- **Deliverables**
  - Drawing sheet management with document attachments and revision tracking.
  - Annotation tooling to overlay weld IDs, heat numbers, and NDE references onto drawing PDFs.
  - Export pipeline spread or structure packages as annotated PDFs bundled with weld dossiers.
  - Reporting enhancements aggregating weld status by drawing/spread.
- **Acceptance Criteria**
  - Users can associate welds with drawing sheets and place annotations with configurable labels.
  - Generated PDFs include annotations at expected coordinates and link back to weld records.
  - System maintains revision history of annotated drawings and prevents stale annotations on superseded sheets.
  - Summary reports show weld completion and NDE pass rates by drawing or area.

### Phase 4: Compliance Automation & Integrations (future, 8+ weeks)
- **Deliverables**
  - Advanced validation rules (e.g., thickness/position compatibility checks, consumable traceability).
  - API endpoints/webhooks for integration with ERP or field data capture tools.
  - Optional offline/mobile data capture module for field weld logging.
- **Acceptance Criteria**
  - Automated checks flag non-conforming weld entries before sign-off.
  - External systems can push/pull weld status via authenticated API endpoints.
  - Offline module syncs data without conflicts, respecting permissions.

## Next Steps
1. Confirm outstanding clarifications to finalize data model and workflows.
2. Create detailed UI wireframes for critical forms (weld joint, NDE workflow, annotation tool).
3. Prioritize integrations and automation features based on stakeholder input.
4. Establish implementation roadmap with resource assignments and sprint planning.
