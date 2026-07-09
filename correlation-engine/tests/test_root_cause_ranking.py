from ranking.ranking_service import RCARankingService

from ranking.formatter import RankingFormatter



predictions={

    "database":0.92,

    "payment":0.05,

    "order":0.03

}



shap={

    "database":0.8,

    "payment":0.1,

    "order":0.1

}



temporal={

    "database":True

}



graph={

    "database":0.9,

    "payment":0.4,

    "order":0.2

}



service = RCARankingService()


ranking = service.rank(

    predictions,

    shap,

    temporal,

    graph

)



formatter = RankingFormatter()


print(

    formatter.format(
        ranking
    )

)