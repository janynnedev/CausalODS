import shap



class SHAPExplainer:


    def __init__(
        self,
        model
    ):

        self.model = model

        self.explainer = (
            shap.TreeExplainer(
                model
            )
        )



    def explain(
        self,
        X
    ):


        shap_values = (

            self.explainer.shap_values(
                X
            )

        )


        return shap_values