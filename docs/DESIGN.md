# 🏗️ Architecture & Technical Design — DESIGN.md


## 📐 System Overview

The system follows a **pipeline → event file → API → dashboard** architecture, cleanly separating computer vision from analytics.

```mermaid
graph LR
    subgraph INPUT["📹 Input Layer"]
        C1["CAM 1 — North Wall"]
        C2["CAM 2 — South Wall"]
        C3["CAM 3 — Entry Door"]
        C4["CAM 4 — Stockroom"]
        C5["CAM 5 — Billing"]
    end

    subgraph PIPELINE["⚙️ Detection Pipeline"]
        direction TB
        D["detect.py<br/>YOLOv8n"]
        T["tracker.py<br/>ByteTrack + OSNet + HSV"]
        E["emit.py<br/>Pydantic + JSONL"]
        D --> T --> E
    end

    subgraph STORE["📄 Event Store"]
        J["events.jsonl"]
    end

    subgraph API["🚀 FastAPI"]
        I["Ingestion"]
        M["Metrics"]
        F["Funnel"]
        H["Heatmap"]
        AN["Anomalies"]
        HE["Health"]
    end

    subgraph DATA["🗄️ Database"]
        PG["PostgreSQL / SQLite"]
    end

    subgraph UI["📊 Dashboard"]
        R["React + TypeScript"]
    end

    C1 & C2 & C3 & C4 & C5 --> D
    E --> J
    J --> I
    I --> PG
    PG --> M & F & H & AN & HE
    M & F & H & AN --> R

    style INPUT fill:#1e293b,stroke:#6366f1,color:#e2e8f0
    style PIPELINE fill:#1e293b,stroke:#8b5cf6,color:#e2e8f0
    style STORE fill:#1e293b,stroke:#a855f7,color:#e2e8f0
    style API fill:#1e293b,stroke:#ec4899,color:#e2e8f0
    style DATA fill:#1e293b,stroke:#14b8a6,color:#e2e8f0
    style UI fill:#1e293b,stroke:#f59e0b,color:#e2e8f0
```

---

## 📹 Camera Coverage Map

The store has **5 CCTV cameras** covering all customer-facing areas plus the staff stockroom:

```mermaid
block-beta
    columns 5

    block:NORTH:5
        columns 5
        KBEAUTY["🧴 K-Beauty<br/>FarmStay · TFS"]
        CLEAN["✨ Clean Beauty<br/>Good Vibes"]
        SKINCARE["💧 Skincare<br/>DermaCo · Minimalist"]
        AQUA["🌊 Aqualogica"]
        FRAG["🌸 Fragrance<br/>Table"]
    end

    space:2
    FOH["💄 F.O.H<br/>Makeup Units"]
    space
    BILLING["💳 Cash Counter<br/>CAM 5"]

    space:4
    STOCK["📦 Stockroom<br/>CAM 4<br/>Staff Only"]

    block:SOUTH:4
        columns 4
        COLOR["💋 Color Cosmetics<br/>Maybelline · Faces"]
        MAKEUP["💅 Makeup<br/>Lakme · Swiss Beauty"]
        HAIR["💇 Haircare<br/>Alps · L'Oréal"]
        NAILS["💅 Nail &<br/>Promo Table"]
    end

    space

    block:ENTRY:5
        columns 1
        DOOR["🚪 ENTRY — CAM 3 — Glass Door + Purplle Signage"]
    end

    NORTH --> NORTH
    SOUTH --> SOUTH

    style NORTH fill:#6366f1,stroke:#4f46e5,color:#fff
    style SOUTH fill:#8b5cf6,stroke:#7c3aed,color:#fff
    style ENTRY fill:#10b981,stroke:#059669,color:#fff
    style BILLING fill:#f59e0b,stroke:#d97706,color:#fff
    style STOCK fill:#ef4444,stroke:#dc2626,color:#fff
```

| Camera | ID | Coverage | FPS | Resolution |
|--------|----|----------|-----|------------|
| CAM 1 | `CAM_FLOOR_01` | North wall — FarmStay, TFS, Good Vibes, DermaCo, Minimalist, Aqualogica | 30 | 1920×1080 |
| CAM 2 | `CAM_FLOOR_02` | South wall — Maybelline, Faces, Lakme, Swiss Beauty, Alps, L'Oréal | 30 | 1920×1080 |
| CAM 3 | `CAM_ENTRY_01` | Store entrance — glass door, Purplle sign, entry mat | 30 | 1920×1080 |
| CAM 4 | `CAM_BACKOFFICE` | Stockroom — staff-only area, storage shelves | 25 | 1920×1080 |
| CAM 5 | `CAM_BILLING_01` | Cash counter — POS terminal, accessories display | 25 | 1920×1080 |

---

## 🔄 Per-Frame Data Flow

Every video frame goes through this processing chain:

```mermaid
sequenceDiagram
    participant F as 📹 Video Frame
    participant Y as 🔍 YOLOv8n
    participant B as 🏃 ByteTrack
    participant O as 🆔 OSNet Re-ID
    participant H as 👔 HSV Staff Classifier
    participant Z as 📍 Zone Detector
    participant P as ✅ Pydantic Validator
    participant J as 📝 JSONL Writer

    F->>Y: Read frame from video
    Y->>B: Bounding boxes + raw confidence
    B->>B: Assign persistent track IDs<br/>(Kalman filter for occlusion)
    B->>O: Person crop → 512-d embedding
    O->>O: Cosine similarity against<br/>shared Re-ID gallery
    Note over O: Gallery is shared<br/>across ALL 5 cameras
    O->>H: Torso crop for HSV analysis
    H->>H: Black uniform detection<br/>+ CAM4 stockroom check<br/>+ majority voting
    O->>Z: Test bbox center against<br/>zone polygons
    Z->>Z: Detect zone transitions:<br/>ENTER → DWELL → EXIT
    H->>P: is_staff flag
    Z->>P: Zone events + dwell_ms
    P->>P: UUID v4 generation<br/>+ schema validation
    P->>J: Write JSON line to events.jsonl
```

---

## 📍 Zone Detection Logic

```mermaid
stateDiagram-v2
    [*] --> Outside: Person detected

    Outside --> EntryZone: Enters CAM3 frame<br/>(right → left)
    EntryZone --> Inside: Crosses entry boundary
    Inside --> ZoneA: Enters zone polygon
    ZoneA --> Dwelling: Stay > 3s
    Dwelling --> ZoneA: ZONE_DWELL emitted
    ZoneA --> Inside: Exits zone polygon<br/>ZONE_EXIT emitted
    Inside --> BillingZone: Enters CAM5<br/>billing polygon
    BillingZone --> Queued: Stay > 3s<br/>BILLING_QUEUE_JOIN
    BillingZone --> Inside: Leave < 3s<br/>BILLING_QUEUE_ABANDON
    Inside --> EntryZone: Walks toward exit
    EntryZone --> Outside: EXIT emitted
    Outside --> EntryZone: Returns<br/>REENTRY emitted

    state "🟢 ENTRY" as EntryZone
    state "🏪 Inside Store" as Inside
    state "📍 Product Zone" as ZoneA
    state "⏱️ Dwelling" as Dwelling
    state "💳 Billing Zone" as BillingZone
    state "⏳ Queued" as Queued
```

| Zone Type | Events Generated | Trigger Condition |
|-----------|-----------------|-------------------|
| `entry_exit` | `ENTRY`, `EXIT`, `REENTRY` | Person crosses entry zone boundary in CAM 3 |
| `dwell` | `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL` | Person enters/exits zone polygon; `DWELL` if stay > 3s |
| `billing` | `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON` | Person enters billing zone in CAM 5; `ABANDON` if leave < 3s |
| `staff_only` | *(staff flag)* | All persons detected in CAM 4 → `is_staff = True` via Re-ID propagation |

---

## 🛡️ Edge Case Handling — All 7 Covered

```mermaid
graph TB
    subgraph EC["🛡️ Edge Case Handlers"]
        direction TB

        EC1["👥 1. Group Entry"]
        EC2["👔 2. Staff Detection"]
        EC3["🔄 3. Re-entry"]
        EC4["🫥 4. Occlusion"]
        EC5["🛒 5. Billing Queue"]
        EC6["⏸️ 6. Empty Periods"]
        EC7["📷 7. Camera Overlap"]
    end

    EC1 --> M1["≥2 ENTRY events within 2s from CAM_ENTRY_01<br/>Each person gets unique visitor_id"]
    EC2 --> M2["HSV black-uniform classification<br/>+ CAM4 stockroom confirmation<br/>+ majority voting across frames"]
    EC3 --> M3["OSNet Re-ID match (cosine > 0.7)<br/>Same visitor_id reused<br/>REENTRY event emitted"]
    EC4 --> M4["ByteTrack Kalman filter predicts through occlusion<br/>Low-confidence detections rescued (Hard Rule #4)"]
    EC5 --> M5["CAM5 billing zone polygon<br/>JOIN/ABANDON events<br/>queue_depth tracked in metadata"]
    EC6 --> M6["No detections for > 30 min<br/>Anomaly auto-logged"]
    EC7 --> M7["Shared Re-ID gallery across all 5 cameras<br/>Same visitor_id prevents double-counting"]

    style EC fill:#1e293b,stroke:#6366f1,color:#e2e8f0
    style M1 fill:#0f172a,stroke:#334155,color:#94a3b8
    style M2 fill:#0f172a,stroke:#334155,color:#94a3b8
    style M3 fill:#0f172a,stroke:#334155,color:#94a3b8
    style M4 fill:#0f172a,stroke:#334155,color:#94a3b8
    style M5 fill:#0f172a,stroke:#334155,color:#94a3b8
    style M6 fill:#0f172a,stroke:#334155,color:#94a3b8
    style M7 fill:#0f172a,stroke:#334155,color:#94a3b8
```

### Camera Overlap Pairs

| Pair | Overlap Region | Description |
|------|---------------|-------------|
| CAM 1 ↔ CAM 2 | F.O.H center | Fragrance/promo table visible from both floor cameras |
| CAM 5 ↔ CAM 4 | Stockroom doorway | Doorway behind cash counter leads to stockroom |
| CAM 3 ↔ CAM 1 | Entry inside | Top-left of CAM 3 overlaps left edge of CAM 1 |
| CAM 3 ↔ CAM 2 | Entry south | Entry area near Maybelline section, partially in CAM 2 |

> **Resolution:** The OSNet Re-ID gallery is **shared across all cameras**. When a person is detected in CAM 1 and then appears in CAM 2, cosine similarity > 0.7 matches them to the same `visitor_id`. No double-counting occurs.

---

## 🚀 API Architecture

```mermaid
graph TB
    subgraph CLIENT["Client Layer"]
        DASH["📊 React Dashboard"]
        CURL["🔧 curl / Swagger"]
    end

    subgraph MW["Middleware"]
        TRACE["🔑 Trace ID Generator<br/>UUID per request"]
        LOG["📝 structlog<br/>JSON logging"]
        ERR["⚠️ Error Handler<br/>No stack traces"]
    end

    subgraph ROUTES["API Routes"]
        R1["POST /events/ingest"]
        R2["GET /stores/{id}/metrics"]
        R3["GET /stores/{id}/funnel"]
        R4["GET /stores/{id}/heatmap"]
        R5["GET /stores/{id}/anomalies"]
        R6["GET /health"]
    end

    subgraph DB["Database"]
        PG["PostgreSQL<br/>(Docker)"]
        SQ["SQLite<br/>(Local Dev)"]
    end

    DASH & CURL --> TRACE
    TRACE --> LOG --> ERR
    ERR --> R1 & R2 & R3 & R4 & R5 & R6
    R1 & R2 & R3 & R4 & R5 --> PG
    R1 & R2 & R3 & R4 & R5 --> SQ
    R6 --> PG & SQ

    style CLIENT fill:#1e293b,stroke:#6366f1,color:#e2e8f0
    style MW fill:#1e293b,stroke:#a855f7,color:#e2e8f0
    style ROUTES fill:#1e293b,stroke:#ec4899,color:#e2e8f0
    style DB fill:#1e293b,stroke:#14b8a6,color:#e2e8f0
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Error responses | Structured JSON, never 500 | Hard Rule #7 — no raw stack traces |
| `/health` | Always returns 200 | Hard Rule #6 — even when DB is down, status shows `db: "down"` |
| Ingestion | Idempotent by `event_id` | Hard Rule #5 — POST same payload twice → same DB state |
| Validation | Pydantic v2 | Hard Rule #1 — every event validated against canonical schema |
| Tracing | UUID per request | Every response includes `trace_id` for observability |

---

## 💳 POS Correlation Rule

> A session counts as a **purchase** if the visitor had a billing-zone event within **5 minutes before** a POS transaction timestamp for the same `store_id`.

```mermaid
graph LR
    BQ["BILLING_QUEUE_JOIN<br/>timestamp: T₁"] --> CHECK{"T₂ - T₁ ≤ 300s?"}
    POS["POS Transaction<br/>timestamp: T₂"] --> CHECK
    CHECK -->|Yes| PURCHASE["✅ Counted as Purchase"]
    CHECK -->|No| SKIP["❌ Not a Purchase"]

    style PURCHASE fill:#10b981,stroke:#059669,color:#fff
    style SKIP fill:#ef4444,stroke:#dc2626,color:#fff
```

---

## 🤖 AI-Assisted Decisions

AI was used as an **engineering assistant** during design and implementation. The objective was to accelerate architectural exploration — not to blindly follow suggestions.

### Summary of AI Contributions

```mermaid
graph LR
    A1["🔍 Detection Model<br/>AI suggested YOLOv8n"] -->|Accepted| D1["Speed + deployment<br/>simplicity prioritized"]
    A2["📋 Event Schema<br/>AI suggested business events"] -->|Accepted| D2["Clean abstraction<br/>between CV and analytics"]
    A3["🏗️ Architecture<br/>AI suggested pipeline separation"] -->|Accepted| D3["Replayability +<br/>easier debugging"]
    A4["👔 Staff Detection<br/>AI suggested rule-based approach"] -->|Partially Accepted| D4["HSV + stockroom +<br/>Re-ID hybrid approach"]

    style A1 fill:#6366f1,stroke:#4f46e5,color:#fff
    style A2 fill:#8b5cf6,stroke:#7c3aed,color:#fff
    style A3 fill:#a855f7,stroke:#9333ea,color:#fff
    style A4 fill:#ec4899,stroke:#db2777,color:#fff
    style D1 fill:#0f172a,stroke:#334155,color:#94a3b8
    style D2 fill:#0f172a,stroke:#334155,color:#94a3b8
    style D3 fill:#0f172a,stroke:#334155,color:#94a3b8
    style D4 fill:#0f172a,stroke:#334155,color:#94a3b8
```

> **Reflection:** AI accelerated implementation and architectural exploration, but all final decisions were reviewed against challenge requirements, observed footage characteristics, deployment constraints, and expected business outcomes.

---

## 🐳 Deployment

```mermaid
graph LR
    GIT["git clone"] --> ENV["cp .env.example .env"]
    ENV --> UP["docker compose up --build"]
    UP --> PG["PostgreSQL 15<br/>auto-migrated"]
    UP --> WEB["FastAPI<br/>port 8000"]
    UP --> DASH["React Dashboard<br/>port 5173"]

    style GIT fill:#1e293b,stroke:#6366f1,color:#e2e8f0
    style ENV fill:#1e293b,stroke:#8b5cf6,color:#e2e8f0
    style UP fill:#1e293b,stroke:#10b981,color:#e2e8f0
    style PG fill:#4169e1,stroke:#2563eb,color:#fff
    style WEB fill:#009688,stroke:#0d9488,color:#fff
    style DASH fill:#61dafb,stroke:#38bdf8,color:#000
```

**Zero manual steps after `git clone`.** Database migrations run automatically on container start. No hardcoded ports — all read from environment variables.
