# 🧬 FitnessOS: Deterministic Physiological Tracking

FitnessOS is a deterministic state engine designed for human performance optimization. It transforms unstructured physiological logs into a structured, persistent state that VERA can use to provide intelligent health and fitness recommendations.

## 🏗️ Architecture: The Reducer Pattern

FitnessOS follows a strict **State-Event-Reducer** architecture to ensure predictability and avoid the hallucinations often associated with LLM-managed state.

### 1. The State (Single Source of Truth)
The state is stored as a JSON fact in AIDE's memory (`fitness_os_state`). It contains:
- **Profile**: Basic user metrics (age, weight, fitness level).
- **Fasting**: Current mode (fasting/eating), timestamps, and target windows.
- **Nutrition**: Cumulative daily calories and a list of meals.
- **Hydration**: Current water intake vs. daily target.
- **Workouts**: A log of all exercises performed today.
- **Muscles**: A counter for volume/intensity per muscle group (chest, back, legs, etc.).
- **Sync Metadata**: `last_sync_timestamp` to track the last time VERA audited conversational history.

### 2. The Event (Typed Input)
Input is never applied directly to the state. It is first passed through a **Parser** that converts raw text into a typed **Event**.
- **Examples**: 
  - `"Hydration 5"` $\rightarrow$ `HYDRATION_LOG(5)`
  - `"Chicken Bowl, 650"` $\rightarrow$ `MEAL_LOG(name="Chicken Bowl", calories=650)`
  - `"Push-ups, 20, chest"` $\rightarrow$ `WORKOUT_LOG(name="Push-ups", value=20, unit="reps", muscle="chest")`

### 3. The Reducer (Pure Logic)
The **Reducer** is a pure function: `(CurrentState, Event) \rightarrow NextState`. 
It applies the event to the state using deterministic logic (e.g., adding calories to the total, incrementing muscle counts) and returns a new state object.

---

## 🔄 VERA Integration & Synchronization

Vera interacts with FitnessOS as both an operator and an analyst.

### The Sync Loop (Avoids Double Entry)
To prevent duplicate logs when VERA audits chat history, FitnessOS uses a **Timestamp Marker** system:

1. **Request**: The UI (Fit Genie/FitnessOS) sends a sync request to VERA including the `last_sync_timestamp`.
2. **Audit**: VERA scans conversational history specifically for fitness events occurring **after** that timestamp.
3. **Commit**: VERA logs these events using the `fitness_os` tool.
4. **Mark**: After all events are logged, VERA calls `fitness_os` with a `sync_marker` containing the current time.
5. **Result**: The `last_sync_timestamp` is updated, ensuring subsequent syncs only process brand-new data.

### VERA Decisions
Once the state is updated, VERA analyzes the `VeraContext` (a compressed version of the state) to generate `VeraCommands` (e.g., "Drink 2 glasses of water", "Start fasting now").

---

## 🔌 Technical Interface

### API Endpoints
- `GET /api/fitness-os/state`: Retrieves the current physiological state.
- `POST /api/fitness-os/dispatch`: Accepts raw input, parses it into an event, reduces the state, and returns the updated state.

### Obsidian Integration
Every state change is mirrored to the Obsidian vault at `FitnessOS/UserState.md` for permanent, human-readable record keeping.

## 🛠️ Summary of Data Flow
`Raw Input` $\rightarrow$ `Parser` $\rightarrow$ `Event` $\rightarrow$ `Reducer` $\rightarrow$ `State` $\rightarrow$ `VeraContext` $\rightarrow$ `Vera Decisions`
