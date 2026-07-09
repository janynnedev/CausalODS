class ExplanationFormatter:



    def format(

        self,

        shap_values,

        feature_names

    ):


        explanation=[]



        for name,value in zip(

            feature_names,

            shap_values

        ):


            explanation.append({

                "feature":
                    name,


                "impact":
                    float(value),


                "direction":

                    "positive"

                    if value > 0

                    else "negative"

            })



        return sorted(

            explanation,

            key=lambda x:

                abs(
                    x["impact"]
                ),

            reverse=True

        )