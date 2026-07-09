import random



class ScenarioGenerator:



    SCENARIOS = [

        "database_latency",

        "payment_failure",

        "cache_failure",

        "kafka_delay"

    ]



    def generate(self):


        return random.choice(
            self.SCENARIOS
        )