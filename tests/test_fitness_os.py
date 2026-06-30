import json

from tools.fitness_os import FitnessOS


class FakeMemory:
    def __init__(self):
        self.facts = {}

    def get_fact(self, key):
        return self.facts.get(key)

    def store_fact(self, key, value):
        self.facts[key] = value


def test_workout_input_is_not_parsed_as_meal():
    fitness = FitnessOS(FakeMemory())

    event = fitness.parse_input("Push-ups, 20, chest")

    assert event["type"] == "WORKOUT_LOG"
    assert event["payload"]["name"] == "Push-ups"
    assert event["payload"]["value"] == 20
    assert event["payload"]["muscle"] == "chest"


def test_meal_input_still_parses_with_tags():
    fitness = FitnessOS(FakeMemory())

    event = fitness.parse_input("Chicken Bowl, 650, high-protein")

    assert event["type"] == "MEAL_LOG"
    assert event["payload"]["name"] == "Chicken Bowl"
    assert event["payload"]["calories"] == 650
    assert event["payload"]["tags"] == "high-protein"


def test_build_vera_commands_is_deterministic_from_state():
    memory = FakeMemory()
    fitness = FitnessOS(memory)
    state = fitness._get_default_state()
    state["hydration"]["waterGlasses"] = 3
    memory.store_fact(fitness.state_key, json.dumps(state))

    commands = fitness.build_vera_commands(fitness.get_state())

    assert commands
    assert commands[0]["action"] == "HYDRATION"
    assert "5 left" in commands[0]["value"]
