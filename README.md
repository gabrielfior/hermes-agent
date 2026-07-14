# AI Doctor

**Privacy-first AI for personalized medicine and treatment plans.**

---

## Vision
AI is approaching a "medical superintelligence" threshold. The smartest way to be ready is to accumulate a **longitudinal health data layer** that feeds into future diagnostic models. We build that bridge today.

**The Asset:** A structured, encrypted memory of your health.
**The Constraint:** Privacy. Your data belongs to you, not a cloud provider.

---

## Core Philosophy

1.  **Universal Ingestion:** We pull from wearables, labs, apps, and manual logs. We normalize everything to a clinical schema (LOINC/SNOMED).
2.  **Zero-Knowledge Vault:** We encrypt your data locally (AES-256) before it ever leaves your device. We can't read it, even if we wanted to.
3.  **Actionable Intelligence:** We don't just show you charts. We use AI to find correlations (e.g., "Your inflammation markers rise when sleep is <6h") and suggest treatments.
4.  **Future-Proof:** You own the data. When the future arrives, you export a standardized "Health Data Passport" to any doctor or AI model.

---

## Technical Architecture

### The Memory Layer
A unified graph of your health, not just spreadsheets.
*   **VitalEvents:** Time-series data (BP, HR, Glucose, Temp).
*   **Biomarkers:** Lab results linked to reference ranges.
*   **Prescriptions:** Meds, dosages, adherence tracking.
*   **Lifestyle:** Sleep, Diet, Exercise, Stress logs.

### Security Model
*   **At Rest (Device):** SQLCipher (AES-256). Key derived from biometric/passphrase.
*   **At Rest (Cloud):** E2EE. Encrypted blobs in S3.
*   **In Transit:** Signal Protocol (Double Ratchet).
*   **In Transit (AI):** Ephemeral context. We stream *only* what's needed for the specific query, then delete it.

### Stack
*   **App:** Flutter / React Native
*   **Local DB:** SQLite + SQLCipher
*   **Backend:** Go or Node (Fastify)
*   **Storage:** AWS S3 (Private, encrypted)
*   **AI:** Hybrid
    *   *Local:* Llama 3 (8b) via MLX/Metal (Privacy-first reasoning)
    *   *Cloud:* OpenRouter (Claude/GPT-4o) for complex correlation

---

## Phased Rollout

### Phase 1: The Vault (Weeks 1-6)
*   **Goal:** Ingestion & Storage.
*   **Features:** Apple Health/Google Fit sync, manual entry, basic dashboard.
*   **Metric:** 50 beta users, 7-day retention >60%.

### Phase 2: Personalization (Months 2-3)
*   **Goal:** The "Memory" & Correlation Engine.
*   **Features:** AI insights ("X causes Y"), treatment planner, evidence grading.
*   **Metric:** Treatment adherence >70%.

### Phase 3: Clinical + Multi-User (Months 4-6)
*   **Goal:** Clinician portal & Compliance.
*   **Features:** HIPAA/GDPR audit trails, FHIR server for doctor integration.
*   **Metric:** 3-5 clinic partners.

### Phase 4: Data Dividend (Year 1+)
*   **Goal:** Research & Licensing.
*   **Features:** Anonymized data marketplace, API for medical AI labs.
*   **Metric:** Research partnerships.

---

## Market & Competition

### Global Landscape
*   **The Gap:** Existing apps are either transparent (Apple Health) or dumb (Excel/Notion trackers). We own the middle: **Private + Intelligent**.
*   **Key Players:** Apple Health (walled garden), K Health (cloud-only, no memory), Babylon (closed loop, no data ownership).

### Brazilian Landscape (Brazil)
*   **The Opportunity:** 30%+ of Brazilians have hypertension. A personalized AI treatment planner for chronic disease is a massive market.
*   **The Gap:** No Brazilian health app offers E2EE or longitudinal AI planning.
*   **Differentiation:** Localized for SUS/Private insurance interoperability, Portuguese-native AI.

---

## Next Steps (Immediate)

1.  **Define MVP Schema:** 50 baseline biomarkers + top 3 integrations.
2.  **Encryption Wrapper:** Build the `Vault` class (encrypt/decrypt logic).
3.  **Local App Skeleton:** Flutter/React Native app with secure local storage.
4.  **Beta Recruiting:** Target biohackers and chronic condition patients.

---

*Status: Alpha Development*
*Privacy: Zero-Knowledge Architecture*
