import shap



class SHAPVisualization:



    def summary_plot(

        self,

        explainer,

        X

    ):


        shap_values = (

            explainer.shap_values(
                X
            )

        )


        shap.summary_plot(

            shap_values,

            X

        )