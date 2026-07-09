from shap_analysis.explainer import SHAPExplainer

from shap_analysis.explanation_formatter import ExplanationFormatter



class SHAPService:



    def __init__(

        self,

        model

    ):


        self.explainer = SHAPExplainer(
            model
        )

        self.formatter = ExplanationFormatter()



    def explain(

        self,

        X

    ):


        shap_values = (

            self.explainer.explain(
                X
            )

        )


        result=[]



        for values in shap_values:


            result.append(

                self.formatter.format(

                    values,

                    X.columns

                )

            )


        return result