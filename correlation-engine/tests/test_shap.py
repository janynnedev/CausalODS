import joblib

from training.data_loader import DataLoader

from training.preprocessing import Preprocessor

from shap_analysis.shap_service import SHAPService



model = joblib.load(

    "models/xgboost_rca.pkl"

)



loader = DataLoader()


df = loader.load(

    "results_rca_dataset.csv"

)



preprocessor = Preprocessor()


X,y = preprocessor.prepare(
    df
)



sample = X.iloc[
    :5
]



service = SHAPService(

    model

)



result = service.explain(

    sample

)



print(

    result[0]

)