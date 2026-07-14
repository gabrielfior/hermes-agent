# Architecture: Zero-Knowledge Health Vault

The guiding principle is: **We encrypt it, you own the key. The AI can only see what you choose to show it.**

## 1. The Encryption Model

| Feature | Implementation | Why |
| :--- | :--- | :--- |
| **At Rest (Device)** | AES-256 encryption of local DB. Key derived from user biometric/passphrase (PBKDF2). | Even if phone is stolen, data is unreadable. |
| **At Rest (Cloud)** | End-to-End Encryption (E2EE). Files encrypted on device before upload. | Even if our servers are hacked, data is useless. |
| **In Transit (AI)** | **Ephemeral Context.** We stream specific data to the LLM for analysis (e.g., "Review these lipid trends") but **never** the full raw dataset. | Minimizes exposure surface. The AI session burns and dies. |
| **In Transit (Sync)** | mTLS (mutual TLS) + Signal Protocol (Double Ratchet) for message encryption. | Unhackable transport layer. |

## 2. The "Memory Layer" (Data Schema)

We need a unified graph, not just spreadsheets. Every detail must be tagged and timestamped to allow for longitudinal correlation.

### Core Entities
1.  **`VitalEvent`**: Time-series data (BP, HR, Glucose, Temp). *Linked to Source.*
2.  **`Biomarker`**: Lab results (Lipid panel, HbA1c). *Linked to Reference Range.*
3.  **`Prescription`**: Meds taken, dosages, adherence.
4.  **`Symptom`**: User-reported (Pain, Fatigue, Mood). *Linked to Vitals.*
5.  **`Lifestyle`**: Sleep, Diet, Exercise, Stress.

### Key Relationship
> "When `Lifestyle.Sleep` < 6h (Event A), then `Vital.HRV` drops 15% (Event B) and `Symptom.Fatigue` rises." -> **AI detects this pattern.**

## 3. Technical Stack

| Component | Tech Choice | Why |
| :--- | :--- | :--- |
| **Mobile App** | **Flutter** or **React Native** | Shared codebase for iOS/Android. |
| **Local DB** | **SQLite** with **SQLCipher** | Industry standard for secure local storage. |
| **Crypto** | **libsodium** (via libsodium.js) | Faster and more secure than standard AES libraries. |
| **Backend** | **Go (Gin)** or **Node (Fastify)** | Lightweight for handling encrypted blobs. No PII in DB. |
| **Storage** | **S3** (Private) | Cheap, scalable, encrypted at rest. |
| **AI Engine** | **Hybrid:** | |
| - Local AI | **Llama 3 (8b)** (via MLX/Metal on iOS/Android) | Privacy: "I'm feeling dizzy." -> Local analysis immediately. |
| - Cloud AI | **OpenRouter** (Claude/GPT-4o) | Complex correlation (e.g., "My labs vs. my sleep"). |
