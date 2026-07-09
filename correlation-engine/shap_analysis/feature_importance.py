import numpy as np



class FeatureImportance:



    def calculate(

        self,

        shap_values,

        feature_names

    ):


        importance = (


            np.abs(
                shap_values
            )

            .mean(
                axis=0
            )

        )



        result=[]



        for name,value in zip(

            feature_names,

            importance

        ):


            result.append({

                "feature":name,

                "importance":
                    float(value)

            })



        return sorted(

            result,

            key=lambda x:
                x["importance"],

            reverse=True

        )