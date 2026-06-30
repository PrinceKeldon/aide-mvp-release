import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Union, List, Dict, Any
from loguru import logger
from core.settings import settings
from memory.manager import MemoryManager

# --- Types ---
# UserState: The single source of truth.
# Event: Typed, immutable record of something that happened.

class FitnessOS:
    def __init__(self, memory: MemoryManager):
        self.name = "fitness_os"
        self.description = "Deterministic physiological tracking engine for human performance optimization."
        self.memory = memory
        self.state_key = "fitness_os_state"
        self.vault_path = Path(settings.obsidian_vault_path).expanduser() / "FitnessOS"

    def get_state(self) -> Dict[str, Any]:
        """Fetch current UserState from memory with daily reset check."""
        raw = self.memory.get_fact(self.state_key)
        if not raw:
            return self._get_default_state()
        try:
            state = json.loads(raw)
            
            # Daily Reset Logic
            today = datetime.utcnow().date().isoformat()
            if state.get("last_reset_date") != today:
                logger.info(f"Resetting FitGenie daily goals for {today}")
                state["nutrition"]["calories"] = 0
                state["nutrition"]["meals"] = []
                state["hydration"]["waterGlasses"] = 0
                state["workouts"] = []
                # Reset muscle counts for the day
                for m in state["muscles"]:
                    state["muscles"][m] = 0
                state["last_reset_date"] = today
                # Persist the reset state
                self.memory.store_fact(self.state_key, json.dumps(state))
                
            return state
        except json.JSONDecodeError:
            return self._get_default_state()

    def _get_default_state(self) -> Dict[str, Any]:
        now = datetime.utcnow().timestamp()
        return {
            "profile": {
                "age": 0,
                "weight": 0,
                "fitnessLevel": "beginner",
            },
            "fasting": {
                "mode": "fasting",
                "lastMealTimestamp": None,
                "lastStateChange": now,
                "targetFastingHours": 16,
                "targetEatingHours": 8,
                "windowStart": 0,
                "windowEnd": 0,
            },
            "nutrition": {
                "calories": 0,
                "meals": [],
            },
            "hydration": {
                "waterGlasses": 0,
                "target": 8,
            },
            "workouts": [],
            "muscles": {
                "chest": 0,
                "back": 0,
                "legs": 0,
                "shoulders": 0,
                "triceps": 0,
                "core": 0,
            },
            "last_reset_date": datetime.utcnow().date().isoformat(),
            "last_sync_timestamp": 0.0,
        }

    def parse_input(self, input_str: str) -> Optional[Dict[str, Any]]:
        """
        Parser: Raw input -> Typed Event.
        Supports both comma-separated values and JSON.
        """
        s = input_str.strip()
        if not s:
            return None
        low = s.lower()

        # 1. Handle JSON input first
        if s.startswith('{') and s.endswith('}'):
            try:
                data = json.loads(s)
                if not isinstance(data, dict):
                    return None
                
                # Support both "type" and "action" keys
                etype = data.get("type") or data.get("action")
                payload = data.get("payload") or data
                
                action_map = {
                    "log_meal": "MEAL_LOG",
                    "meal_log": "MEAL_LOG",
                    "log_hydration": "HYDRATION_LOG",
                    "hydration_log": "HYDRATION_LOG",
                    "log_workout": "WORKOUT_LOG",
                    "workout_log": "WORKOUT_LOG",
                    "fasting_update": "FASTING_UPDATE",
                    "state_edit": "STATE_EDIT",
                    "sync_marker": "SYNC_MARKER"
                }
                
                if etype in action_map:
                    return {"type": action_map[etype], "payload": payload}
            except json.JSONDecodeError:
                pass

        # 2. Handle Comma-Separated Values (CSV)
        # Hydration: "5" or "Hydration 5"
        if low.startswith("hydration") or (s.isdigit() and len(s) < 3):
            val = low.replace("hydration", "").replace(":", "").strip()
            if val.isdigit():
                return {"type": "HYDRATION_LOG", "payload": int(val)}

        # Fasting: "start", "fasting", "end", or "eating"
        if low in {"start", "fasting", "fasting start"}:
            return {"type": "FASTING_UPDATE", "payload": {"mode": "fasting", "timestamp": datetime.utcnow().timestamp()}}
        if low in {"end", "eating", "fasting end"}:
            return {"type": "FASTING_UPDATE", "payload": {"mode": "eating", "timestamp": datetime.utcnow().timestamp()}}

        # Workout: "Push-ups, 40, chest" or "Cycling, 128cal, legs"
        if "," in s and any(char.isdigit() for char in s) and not s.startswith('{'):
            parts = [p.strip() for p in s.split(",")]
            known_muscles = set(self._get_default_state()["muscles"]) | {
                "biceps",
                "arms",
                "glutes",
                "cardio",
                "full body",
            }
            if len(parts) >= 3 and parts[2].lower() in known_muscles:
                try:
                    val_str = parts[1].lower()
                    is_cal = "cal" in val_str or "calorie" in val_str
                    num_val = int(''.join(filter(str.isdigit, val_str)))
                    return {
                        "type": "WORKOUT_LOG",
                        "payload": {
                            "name": parts[0],
                            "value": num_val,
                            "unit": "calories" if is_cal else "reps",
                            "muscle": parts[2].lower()
                        }
                    }
                except (ValueError, IndexError):
                    pass

        # Meal: "Chicken Bowl, 650, high-protein"
        if "," in s and any(char.isdigit() for char in s) and not s.startswith('{'):
            parts = [p.strip() for p in s.split(",")]
            if len(parts) >= 2:
                try:
                    cals_str = ''.join(filter(str.isdigit, parts[1]))
                    if cals_str:
                        return {
                            "type": "MEAL_LOG",
                            "payload": {
                                "name": parts[0],
                                "calories": int(cals_str),
                                "tags": parts[2] if len(parts) > 2 else "general"
                            }
                        }
                except ValueError:
                    pass

        # State Edit: "edit state {json}"
        if low.startswith("edit state"):
            try:
                json_str = s[10:].strip()
                return {"type": "STATE_EDIT", "payload": json.loads(json_str)}
            except json.JSONDecodeError:
                pass

        # Sync Marker: "sync_marker {timestamp}"
        if low.startswith("sync_marker"):
            try:
                ts_str = s[11:].strip()
                return {"type": "SYNC_MARKER", "payload": float(ts_str)}
            except ValueError:
                pass

        return None


    def reduce_state(self, state: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reducer: Pure function. (State, Event) -> Next State.
        """
        next_state = json.loads(json.dumps(state)) # Deep copy
        etype = event.get("type")
        payload = event.get("payload")

        if etype == "MEAL_LOG":
            next_state["nutrition"]["calories"] += payload["calories"]
            next_state["nutrition"]["meals"].insert(0, payload)
        
        elif etype == "WORKOUT_LOG":
            next_state["workouts"].insert(0, payload)
            m = payload["muscle"]
            if m in next_state["muscles"]:
                next_state["muscles"][m] += 1
            else:
                next_state["muscles"][m] = 1
                
        elif etype == "HYDRATION_LOG":
            next_state["hydration"]["waterGlasses"] = payload
            
        elif etype == "FASTING_UPDATE":
            mode = payload["mode"].lower()
            now = payload["timestamp"]
            next_state["fasting"]["mode"] = mode
            next_state["fasting"]["lastStateChange"] = now
            if mode == "eating":
                next_state["fasting"]["lastMealTimestamp"] = now
        
        elif etype == "STATE_EDIT":
            # Merge provided payload into state. 
            # If payload is a dict, we merge it. If it's a full state, we replace.
            if isinstance(payload, dict):
                for k, v in payload.items():
                    if k == "fasting" and isinstance(v, dict) and "mode" in v:
                        v["mode"] = str(v["mode"]).lower()
                    
                    if k in next_state and isinstance(next_state[k], dict) and isinstance(v, dict):
                        next_state[k].update(v)
                    else:
                        next_state[k] = v
        
        elif etype == "SYNC_MARKER":
            next_state["last_sync_timestamp"] = payload

        return next_state

    def build_vera_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Context Builder: Compresses state to AIDEContext.
        """
        return {
            "fasting": state["fasting"]["mode"],
            "fasting_elapsed_sec": datetime.utcnow().timestamp() - state["fasting"]["lastStateChange"],
            "calories": state["nutrition"]["calories"],
            "hydration": state["hydration"]["waterGlasses"],
            "muscles": state["muscles"],
            "workoutsToday": len(state["workouts"]),
            "lastMealTime": state["fasting"]["lastMealTimestamp"],
        }

    def build_vera_commands(self, state: Dict[str, Any]) -> list[Dict[str, str]]:
        """Deterministic guidance from the current state for the Fit Genie UI."""
        ctx = self.build_vera_context(state)
        commands: list[Dict[str, str]] = []

        hydration_target = state.get("hydration", {}).get("target", 8) or 8
        hydration_gap = max(0, hydration_target - ctx["hydration"])
        if hydration_gap:
            commands.append({
                "action": "HYDRATION",
                "value": f"Drink {min(2, hydration_gap)} glass(es) now; {hydration_gap} left for today's target.",
            })

        if ctx["calories"] == 0:
            commands.append({
                "action": "NUTRITION",
                "value": "No meals logged today. Log the next meal with calories so the state stays useful.",
            })

        if ctx["workoutsToday"] == 0:
            commands.append({
                "action": "TRAINING",
                "value": "No workout logged today. Add one focused session or mark today as rest.",
            })

        mode = state.get("fasting", {}).get("mode", "fasting")
        target_hours = (
            state.get("fasting", {}).get("targetFastingHours", 16)
            if mode == "fasting"
            else state.get("fasting", {}).get("targetEatingHours", 8)
        )
        elapsed_hours = max(0, ctx["fasting_elapsed_sec"]) / 3600
        if elapsed_hours >= target_hours:
            commands.append({
                "action": "FASTING",
                "value": f"{mode.capitalize()} window target reached. Decide whether to switch modes or extend deliberately.",
            })

        return commands[:4]

    def _sync_to_obsidian(self, state: Dict[str, Any]):
        try:
            self.vault_path.mkdir(parents=True, exist_ok=True)
            file = self.vault_path / "UserState.md"
            content = [
                "# 🧬 FitnessOS State",
                f"\n**Last Sync**: {datetime.utcnow().isoformat()}",
                "\n## Fasting",
                f"- Mode: {state['fasting']['mode']}",
                f"- Last Meal: {state['fasting']['lastMealTimestamp']}",
                "\n## Nutrition",
                f"- Total Calories: {state['nutrition']['calories']}",
                "\n## Hydration",
                f"- Water: {state['hydration']['waterGlasses']} / {state['hydration']['target']}",
                "\n## Muscles",
            ]
            for m, v in state["muscles"].items():
                content.append(f"- {m.capitalize()}: {v}")
            
            file.write_text("\n".join(content), encoding="utf-8")
        except Exception as e:
            logger.warning(f"FitnessOS Obsidian sync error: {e}")

    def execute(self, input_str: str) -> str:
        """
        Main tool entry point.
        """
        # 1. Parse
        event = self.parse_input(input_str)
        if not event:
            # If no event, just return the current context for AIDE to make a decision
            state = self.get_state()
            ctx = self.build_vera_context(state)
            return f"No new event detected. Current AIDEContext: {json.dumps(ctx)}"

        # 2. Reduce
        state = self.get_state()
        next_state = self.reduce_state(state, event)

        # 3. Store
        self.memory.store_fact(self.state_key, json.dumps(next_state))
        self._sync_to_obsidian(next_state)

        # 4. Context for AIDE
        ctx = self.build_vera_context(next_state)
        
        return f"Event {event['type']} processed. New AIDEContext: {json.dumps(ctx)}. AIDE should now return AIDECommands."
