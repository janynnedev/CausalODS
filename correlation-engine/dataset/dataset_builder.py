from dataset.scenario_generator import ScenarioGenerator
from dataset.feature_generator import FeatureGenerator
from dataset.label_generator import LabelGenerator



class DatasetBuilder:



    def __init__(self):

        self.scenario_generator = ScenarioGenerator()

        self.feature_generator = FeatureGenerator()

        self.label_generator = LabelGenerator()



    def generate(
        self,
        samples:int=1000
    ):


        dataset=[]



        services=[

            "frontend",

            "order",

            "payment",

            "database",

            "cache"

        ]



        for _ in range(samples):


            scenario = (
                self.scenario_generator.generate()
            )


            for service in services:


                feature = (

                    self.feature_generator.generate(

                        scenario,

                        service

                    )

                )


                feature["root_cause"] = (

                    self.label_generator.generate(

                        feature,

                        scenario

                    )

                )


                dataset.append(
                    feature
                )



        return dataset