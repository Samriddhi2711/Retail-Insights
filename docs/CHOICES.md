# 🧭 Design Decisions & Assumptions — CHOICES.md

This document records every major decision, assumption, and tradeoff made during the design and implementation of the Store Intelligence system. Each decision includes the context, options evaluated, final choice, and rationale.

---

## 📑 Table of Contents

| # | Decision | Category |
|---|----------|----------|
| 1–4 | [Camera Mapping & Clip Metadata](#-assumptions-derived-from-asset-analysis) | Asset Analysis |
| 5–8 | [Store Layout & Zones](#-store-layout-decisions) | Layout |
| 9–10 | [Staff Detection Strategy](#-staff-detection-decisions) | Staff |
| 11–12 | [Camera Overlap & Blind Spots](#-camera-overlap-decisions) | Overlap |
| 13–20 | [Technical Architecture](#-technical-decisions) | Architecture |
| 21 | [Detection Model Selection](#-decision-21-detection-model-selection) | Model |
| 22 | [Event Schema Design](#-decision-22-event-schema-design) | Schema |
| 23 | [API Architecture](#-decision-23-api-architecture) | API |
| — | [AI-Assisted Decisions](#-ai-assisted-decisions) | AI Usage |

---

## 📹 Assumptions Derived from Asset Analysis

### Decision 1 — Camera Mapping

Camera names (CAM 1–5) were **not labeled** in the footage metadata. Mapping was inferred from visible brand signage, fixtures, and comparison with the store layout diagram.

```mermaid
graph LR
    subgraph CAMERAS["📹 Camera → Zone Mapping"]
        C1["CAM 1.mp4"] -->|"Brand signage:<br/>FarmStay, TFS, DermaCo"| Z1["CAM_FLOOR_01<br/>North Wall"]
        C2["CAM 2.mp4"] -->|"Brand signage:<br/>L'Oreal, Lakme, Maybelline"| Z2["CAM_FLOOR_02<br/>South Wall"]
        C3["CAM 3.mp4"] -->|"Glass door +<br/>Purplle branding"| Z3["CAM_ENTRY_01<br/>Store Entrance"]
        C4["CAM 4.mp4"] -->|"Purplle boxes,<br/>storage shelves"| Z4["CAM_BACKOFFICE<br/>Stockroom"]
        C5["CAM 5.mp4"] -->|"POS terminal,<br/>Accessories LED"| Z5["CAM_BILLING_01<br/>Cash Counter"]
    end

    style CAMERAS fill:#1e293b,stroke:#6366f1,color:#e2e8f0
    style Z1 fill:#6366f1,stroke:#4f46e5,color:#fff
    style Z2 fill:#8b5cf6,stroke:#7c3aed,color:#fff
    style Z3 fill:#10b981,stroke:#059669,color:#fff
    style Z4 fill:#ef4444,stroke:#dc2626,color:#fff
    style Z5 fill:#f59e0b,stroke:#d97706,color:#fff
```

| File | Camera ID | Coverage | Identification Basis |
|------|-----------|----------|---------------------|
| `CAM 1.mp4` | `CAM_FLOOR_01` | North wall — FarmStay, TFS, Good Vibes, DermaCo, Minimalist, Aqualogica + fragrance table | Brands visible on shelf signage |
| `CAM 2.mp4` | `CAM_FLOOR_02` | South wall — Alps, L'Oréal, Swiss Beauty, Lakme, Faces Canada, Maybelline + nail/promo table | Brand signage confirmed in frame |
| `CAM 3.mp4` | `CAM_ENTRY_01` | Store entrance — glass door, Purplle backlit sign, entry mat, pavement | Glass door + Purplle branding; people enter from right |
| `CAM 4.mp4` | `CAM_BACKOFFICE` | Back office / stockroom — Purplle boxes, storage shelves, water dispenser | Staff-only area; no product shelves |
| `CAM 5.mp4` | `CAM_BILLING_01` | Cash counter — POS terminal, laptop, Accessories LED display | Cashier station clearly visible |

---

### Decision 2 — Clip Start Time

| Parameter | Value | Basis |
|-----------|-------|-------|
| **Start time** | `2026-04-10T20:10:00Z` | CCTV timestamps visible in frames show `10/04/2026 20:10:xx` |
| **Normalization** | All clips start at `20:10:00Z` | Exact seconds vary per camera (10:15, 10:18, 10:27, 10:32, 10:57) — normalized to common baseline |
| **Per-frame timestamp** | `clip_start_time + (frame_index / fps)` | ISO-8601 UTC as required by Detection Rules |

---

### Decision 3 — Frame Rate

> **Confirmed from video metadata** — not an assumption.

| Camera | FPS | Source |
|--------|-----|--------|
| CAM 1, 2, 3 | **30.0** | OpenCV `cv2.CAP_PROP_FPS` |
| CAM 4, 5 | **25.0** | OpenCV `cv2.CAP_PROP_FPS` |

---

### Decision 4 — Resolution

> **Confirmed**: All cameras are **1920×1080** (Full HD). Zone polygons are defined in this coordinate space.

---

## 🏪 Store Layout Decisions

### Decision 5 — Entry/Exit Point

| Aspect | Decision |
|--------|----------|
| **Configuration** | Single entry/exit via glass door on left wall (CAM 3) |
| **Basis** | Store layout shows "Existing Glass" on left with curved entry. "Access" area (bottom-right) is staff-only |
| **Entry direction** | Right-to-left in CAM 3 frame (outside pavement → glass door → store interior) |

---

### Decision 6 — Zone Polygon Coordinates

**Assumption:** Zone polygons are approximated from the 1920×1080 CCTV frames. Estimated by identifying brand signage boundaries, fixture positions, and shelf edges. May need refinement after a test run.

---

### Decision 7 — Billing Counter Location

| Aspect | Decision |
|--------|----------|
| **Camera** | CAM 5 (`CAM_BILLING_01`) |
| **Basis** | Frame shows POS terminal, laptop, counter desk, staff member in black operating cash register |
| **Layout match** | "CASH COUNTER" label on store layout (far right side) |

---

### Decision 8 — Queue Detection

```mermaid
graph LR
    ENTER["Person enters<br/>billing polygon<br/>(x: 0-750 in CAM 5)"] --> CHECK{"Dwell > 3s?"}
    CHECK -->|Yes| JOIN["BILLING_QUEUE_JOIN<br/>queue_depth incremented"]
    CHECK -->|No, leaves| ABANDON["BILLING_QUEUE_ABANDON"]

    style JOIN fill:#10b981,stroke:#059669,color:#fff
    style ABANDON fill:#ef4444,stroke:#dc2626,color:#fff
```

| Aspect | Decision |
|--------|----------|
| **Detection zone** | Customer-facing side of billing counter (left half of CAM 5, x: 0–750) |
| **Queue boundary** | Counter creates natural boundary; customers approach from store floor (left) |
| **Limitation** | No dedicated queue rope visible; queue depth estimated as simultaneous person count inside billing zone |

---

## 👔 Staff Detection Decisions

### Decision 9 — Staff Uniform Color: Black

```mermaid
graph TB
    DETECT["Person Detected"] --> HSV["HSV Torso Analysis"]
    HSV --> PRIMARY{"Primary Range<br/>H:0-180 S:0-100 V:0-80<br/>(Black)"}
    HSV --> SECONDARY{"Secondary Range<br/>H:100-140 S:10-120 V:0-80<br/>(Dark Navy)"}
    PRIMARY --> COVERAGE{"Torso Coverage<br/>≥ 45%?"}
    SECONDARY --> COVERAGE
    COVERAGE -->|Yes| VOTE["Add 'staff' vote"]
    COVERAGE -->|No| NOVOTE["Add 'visitor' vote"]
    VOTE --> MAJORITY{"Majority across<br/>all frames?"}
    NOVOTE --> MAJORITY
    MAJORITY -->|Staff wins| STAFF["is_staff = true ✅"]
    MAJORITY -->|Visitor wins| VISITOR["is_staff = false"]

    style STAFF fill:#f59e0b,stroke:#d97706,color:#fff
    style VISITOR fill:#6366f1,stroke:#4f46e5,color:#fff
```

| Aspect | Decision |
|--------|----------|
| **Method** | HSV color analysis on torso crop |
| **Evidence** | CAM 5 cashier: all-black top/pants. CAM 4 stockroom: dark clothing. CAM 1/2: staff in black near counters |
| **Mitigation** | Majority voting across multiple frames — brief visitors in black won't accumulate enough "staff" votes |
| **Known limitation** | Black is a common visitor color → false positives possible, mitigated by voting |

---

### Decision 10 — CAM 4 as Staff Identifier

| Aspect | Decision |
|--------|----------|
| **Rule** | All persons detected in CAM 4 (`CAM_BACKOFFICE`) are definitively staff |
| **Basis** | Stockroom is clearly staff-only (boxes, storage, no product display) |
| **Propagation** | Combined with Re-ID gallery matching → anyone seen here and later on floor is tagged `is_staff = True` |

---

## 📷 Camera Overlap Decisions

### Decision 11 — Overlap Regions

```mermaid
graph TB
    subgraph OVERLAP["📷 Camera Overlap Map"]
        C1["CAM 1<br/>North Wall"] ---|"F.O.H center table<br/>(fragrance/promo)"| C2["CAM 2<br/>South Wall"]
        C5["CAM 5<br/>Billing"] ---|"Stockroom doorway<br/>(behind counter)"| C4["CAM 4<br/>Stockroom"]
        C3["CAM 3<br/>Entry"] ---|"Entry inside<br/>(top-left overlap)"| C1
        C3 ---|"Entry south<br/>(Maybelline area)"| C2
    end

    REID["🆔 OSNet Re-ID Gallery<br/>Shared across ALL cameras<br/>Cosine similarity > 0.7"] --> DEDUP["Same visitor_id reused<br/>No double-counting"]

    style OVERLAP fill:#1e293b,stroke:#6366f1,color:#e2e8f0
    style REID fill:#8b5cf6,stroke:#7c3aed,color:#fff
    style DEDUP fill:#10b981,stroke:#059669,color:#fff
```

---

### Decision 12 — Known Blind Spots

| Location | Impact | Mitigation |
|----------|--------|------------|
| Far-left corner of north wall (FarmStay edge) | Partial coverage — persons at shelf edge may be partially occluded | ByteTrack Kalman filter predicts through brief occlusion |
| Behind F.O.H makeup unit chairs | Area between chairs hidden from both floor cameras | Accepted — short transit zone, not a dwell area |
| Passage between billing and stockroom | Brief tracking loss during CAM 5 → CAM 4 handoff | Re-ID gallery re-matches on reappearance |

---

## ⚙️ Technical Decisions

### Decision 13 — ByteTrack over BoTSORT

| Aspect | Detail |
|--------|--------|
| **Choice** | ByteTrack (specified in AGENTS.md) |
| **Why** | Excels at handling occlusion by associating **both** high- and low-confidence detections |
| **Critical for** | Dense retail environment with shelving fixtures causing frequent partial occlusion (Edge Case #4) |

---

### Decision 14 — OSNet Re-ID Model

| Aspect | Detail |
|--------|--------|
| **Choice** | `osnet_x1_0` (specified in AGENTS.md) |
| **Why** | 512-dimensional embeddings with good cross-domain generalization |
| **Alternative considered** | `osnet_ain` for domain generalization — standard model performs adequately for single-store |

---

### Decision 15 — HSV over Deep Learning Staff Classification

| Aspect | Detail |
|--------|--------|
| **Choice** | HSV uniform color analysis + majority voting |
| **Why** | No labeled staff training data available; deployable immediately |
| **Supplement** | CAM 4 stockroom provides ground truth for Re-ID-based staff ID |

---

### Decision 16 — Confidence Threshold = 0.0

> **Hard Rule #4:** *"Never suppress low-confidence detections — emit with real confidence value."*

| Aspect | Detail |
|--------|--------|
| **Choice** | Emit ALL detections with raw confidence |
| **Filtering** | Happens at query time in the API layer, never at emission time |

---

### Decision 17 — JSONL Event Emission (not direct API POST)

| Aspect | Detail |
|--------|--------|
| **Choice** | Pipeline writes to `events.jsonl`; API ingestion is a separate step |
| **Basis** | Specified in build-pipeline workflow |
| **Benefits** | Reliability, debuggability, replayability |
| **Future** | Config includes `emit_to_api` flag for direct API posting |

---

### Decision 18 — Session Sequence Numbering

| Aspect | Detail |
|--------|--------|
| **Choice** | `session_seq` is a monotonic counter per `visitor_id`, incremented on each event |
| **Interpretation** | AGENTS.md schema shows `session_seq: 5` → "the 5th event in this visitor's session" |
| **Scope** | Resets across pipeline runs |

---

### Decision 19 — Store Hours & Empty Period Detection

| Aspect | Detail |
|--------|--------|
| **Assumption** | Footage is from 8:10 PM — store operates in evening hours |
| **Threshold** | 30 minutes with no detections → anomaly logged |
| **Note** | May need adjustment based on actual operating hours |

---

### Decision 20 — Entry Direction

| Aspect | Detail |
|--------|--------|
| **Direction** | Right → Left in CAM 3 (outside → inside) |
| **Basis** | Outside pavement is on right, glass door center, store interior left |

---

## 🔍 Decision 21 — Detection Model Selection

### Problem

Detect people reliably across 5 CCTV cameras while remaining deployable on commodity hardware inside Docker containers.

### Options Evaluated

```mermaid
graph TB
    subgraph OPTIONS["Detection Model Candidates"]
        direction LR
        A["Faster R-CNN<br/>❌ Rejected"]
        B["RT-DETR<br/>❌ Rejected"]
        C["YOLOv8s<br/>❌ Rejected"]
        D["YOLOv8n<br/>✅ Selected"]
    end

    A --> A1["High accuracy but<br/>slow inference +<br/>high compute"]
    B --> B1["Strong transformer but<br/>higher latency +<br/>resource intensive"]
    C --> C1["Better detection but<br/>increased memory +<br/>higher latency"]
    D --> D1["Fast startup +<br/>low memory +<br/>Docker-friendly +<br/>reliable person detection"]

    style D fill:#10b981,stroke:#059669,color:#fff
    style A fill:#ef4444,stroke:#dc2626,color:#fff
    style B fill:#ef4444,stroke:#dc2626,color:#fff
    style C fill:#f59e0b,stroke:#d97706,color:#fff
    style D1 fill:#0f172a,stroke:#10b981,color:#86efac
```

### Final Choice: **YOLOv8n**

| Factor | YOLOv8n |
|--------|---------|
| Inference speed | ⚡ Fastest |
| Memory usage | 🟢 Lowest |
| Person detection | ✅ Reliable for medium-to-large instances |
| Docker deployment | ✅ Lightweight image |
| Tradeoff | Slightly lower accuracy vs YOLOv8s — not justified for this scenario |

---

## 📋 Decision 22 — Event Schema Design

### Problem

Raw detections (bounding boxes, track IDs) are difficult to use for business analytics. The API must answer: *How many visited? How many purchased? Which zones get attention? Where do customers drop off?*

### Options Evaluated

```mermaid
graph LR
    subgraph SCHEMA["Schema Design Options"]
        A["Option A<br/>Raw Detections<br/>❌ Rejected"] --> A1["BBoxes, track IDs<br/>Complete but complex analytics<br/>Tight model coupling"]
        B["Option B<br/>Business Events<br/>✅ Selected"] --> B1["ENTRY, EXIT, ZONE_DWELL...<br/>Stable contracts, clean APIs<br/>Model-agnostic"]
    end

    style A fill:#ef4444,stroke:#dc2626,color:#fff
    style B fill:#10b981,stroke:#059669,color:#fff
```

### Final Choice: **Business Event Schema**

The challenge evaluates **retail intelligence**, not computer vision metrics. Business events create a clean abstraction between detection, tracking, analytics, and dashboard visualization — and allow future model changes without affecting API consumers.

---

## 🏗️ Decision 23 — API Architecture

### Problem

How should events flow from the detection pipeline into analytics services?

### Options Evaluated

```mermaid
graph LR
    subgraph ARCH["Architecture Options"]
        A["Option A<br/>Direct DB Writes<br/>❌ Rejected"]
        B["Option B<br/>Event File + API<br/>✅ Selected"]
    end

    A --> A1["Pipeline → DB<br/>Simple but hard to debug,<br/>tight coupling, no replay"]
    B --> B1["Pipeline → events.jsonl<br/>→ POST /events/ingest → DB<br/>Reproducible, replayable, idempotent"]

    style A fill:#ef4444,stroke:#dc2626,color:#fff
    style B fill:#10b981,stroke:#059669,color:#fff
```

### Final Choice: **Pipeline → events.jsonl → Ingestion API → Database**

| Benefit | Description |
|---------|-------------|
| **Observability** | Human-readable event file for debugging |
| **Replayability** | Re-ingest without rerunning the pipeline |
| **Idempotency** | POST same payload twice → same DB state |
| **Decoupling** | Detection pipeline independent from storage |

---

## 🤖 AI-Assisted Decisions

AI was used as an **engineering assistant** throughout development. The objective was not to blindly follow suggestions, but to accelerate exploration of architectures and implementation strategies.

```mermaid
graph TB
    subgraph AI["🤖 AI Contributions"]
        direction TB
        S1["Detection Model"] --> R1["Suggested YOLOv8n for<br/>deployment simplicity"]
        S2["Event Schema"] --> R2["Suggested business events<br/>over raw detections"]
        S3["API Architecture"] --> R3["Suggested separating event<br/>generation from persistence"]
        S4["Staff Detection"] --> R4["Suggested rule-based approach<br/>due to no labeled data"]
    end

    R1 -->|"✅ Accepted"| F1["YOLOv8n selected"]
    R2 -->|"✅ Accepted"| F2["Canonical event schema"]
    R3 -->|"✅ Accepted"| F3["JSONL + ingestion API"]
    R4 -->|"⚡ Partially Accepted"| F4["HSV + stockroom + Re-ID<br/>hybrid approach"]

    style AI fill:#1e293b,stroke:#8b5cf6,color:#e2e8f0
    style F1 fill:#10b981,stroke:#059669,color:#fff
    style F2 fill:#10b981,stroke:#059669,color:#fff
    style F3 fill:#10b981,stroke:#059669,color:#fff
    style F4 fill:#f59e0b,stroke:#d97706,color:#fff
```

| Decision | AI Suggestion | Outcome | Rationale |
|----------|--------------|---------|-----------|
| **Detection model** | YOLOv8n | ✅ Accepted | Best speed/accuracy tradeoff for Docker deployment |
| **Event schema** | Business-level events | ✅ Accepted | Cleaner analytics, stable API contracts |
| **API architecture** | Separate generation & persistence | ✅ Accepted | Replayability, easier debugging |
| **Staff detection** | Lightweight rule-based approach | ⚡ Partially accepted | Extended with stockroom confirmation + Re-ID propagation |

> **Reflection:** AI accelerated implementation and architectural exploration, but all final decisions were reviewed against challenge requirements, observed footage characteristics, deployment constraints, and expected business outcomes.

---

## 🏁 Final Architecture Summary

```mermaid
graph LR
    A["1️⃣ YOLOv8n<br/>Person Detection"] --> B["2️⃣ ByteTrack<br/>Multi-Object Tracking"]
    B --> C["3️⃣ OSNet<br/>Re-Identification"]
    C --> D["4️⃣ HSV + Stockroom<br/>Staff Classification"]
    D --> E["5️⃣ Business Event<br/>Generation"]
    E --> F["6️⃣ FastAPI<br/>Analytics API"]
    F --> G["7️⃣ PostgreSQL<br/>Persistence"]
    G --> H["8️⃣ React<br/>Live Dashboard"]

    style A fill:#6366f1,stroke:#4f46e5,color:#fff
    style B fill:#7c3aed,stroke:#6d28d9,color:#fff
    style C fill:#8b5cf6,stroke:#7c3aed,color:#fff
    style D fill:#a855f7,stroke:#9333ea,color:#fff
    style E fill:#c084fc,stroke:#a855f7,color:#fff
    style F fill:#ec4899,stroke:#db2777,color:#fff
    style G fill:#f43f5e,stroke:#e11d48,color:#fff
    style H fill:#f59e0b,stroke:#d97706,color:#fff
```

The architecture prioritizes **simplicity**, **reproducibility**, **operational visibility**, and **business value** while remaining practical to deploy within the challenge constraints.
