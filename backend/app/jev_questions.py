"""
Conjuntos de preguntas de Jev (TypeSafe) VERSIONADOS y CONGELADOS (PROPUESTA_JEV_CIS.md §4.3, §5).

Regla: una vez que se empieza a mirar resultados, estas preguntas NO se editan -- si hay que
cambiar algo se crea tags_v2 y se re-etiqueta. Con ~12 preguntas y pocos datos, ajustar las
preguntas después de ver los resultados garantiza falsos descubrimientos.

Jev lee texto, no números (su propia doc: "Jev is not a calculator"), así que el estado son solo
nombre/símbolo/cadena del token y una lista fija de proyectos conocidos. Todo en inglés (idioma
donde Jev rinde mejor según su doc).
"""

TAGS_V1 = "tags_v1"

# Lista fija (congelada con tags_v1) de proyectos que los estafadores suelen imitar.
KNOWN_PROJECTS = [
    "Bitcoin", "Ethereum", "BNB", "Solana", "XRP", "Cardano", "Dogecoin", "Shiba Inu", "Pepe",
    "Tether", "USD Coin", "Chainlink", "Uniswap", "Aave", "PancakeSwap", "Polygon", "Avalanche",
    "Polkadot", "Litecoin", "Tron", "Toncoin", "Sui", "Aptos", "Arbitrum", "Optimism", "Base",
    "Cosmos", "Near Protocol", "Filecoin", "Render", "Fetch.ai", "Bittensor", "Worldcoin",
    "Dogwifhat", "Bonk", "Floki", "Trump", "Melania", "Fartcoin", "Binance", "Coinbase",
    "OpenAI", "ChatGPT", "Tesla", "Apple", "Google", "Nvidia", "Microsoft", "Amazon", "Meta",
    "Elon Musk", "Vitalik", "CZ", "SpaceX", "Grok", "DeepSeek", "Wrapped Bitcoin", "Lido",
    "Maker", "Curve", "Ripple",
]

NARRATIVE_OPTIONS = {
    "meme_animal": "A meme token themed on an animal (dog, cat, frog, etc.). NOT a token that only mentions an animal in passing.",
    "meme_person_or_political": "A meme token themed on a real person, celebrity, politician or political movement.",
    "meme_other": "Any other meme or joke token that does not fit the animal or person categories.",
    "ai_agent": "Named around artificial intelligence, AI agents, machine learning or autonomous bots.",
    "defi_or_infra": "Named around decentralized finance, DEX, lending, staking, oracles, bridges, layer-1/2 or blockchain infrastructure.",
    "gaming_or_metaverse": "Named around games, esports, NFTs for games or a metaverse.",
    "rwa": "Represents a real-world asset such as a stock, commodity, bond or real estate.",
    "payments_or_stable": "Named around payments, remittances or a stablecoin.",
    "social_or_creator": "Named around social networks, creators, fan communities or content.",
    "other_unclear": "None of the above, or the name gives no clear signal.",
}

_STATE_NOTE = (
    "Judge only from the token `name`, `symbol` and `chain` in `token`. "
    "`known_projects` is a fixed reference list."
)

TAGS_V1_QUESTIONS = {
    "narrative": {
        "type": "choice",
        "instructions": "What narrative is this token's name and symbol themed on? " + _STATE_NOTE,
        "criteria": NARRATIVE_OPTIONS,
    },
    "impersonates_known": {
        "type": "noul",
        "instructions": "Does the token's name or symbol imitate, copy or closely resemble any project in `known_projects`, without being that project itself?",
        "criteria": {
            "true": "The name or symbol looks like a copy, parody or imitation of a listed project.",
            "false": "The name and symbol are original, or are the listed project itself.",
        },
    },
    "promises_returns": {
        "type": "noul",
        "instructions": "Does the token's name or symbol promise profits, yields, guaranteed returns or price growth (for example 100x, moon, safe, guaranteed, profit)?",
        "criteria": {
            "true": "The name or symbol explicitly promises or advertises gains.",
            "false": "The name and symbol do not promise gains.",
        },
    },
    "is_wrapper": {
        "type": "noul",
        "instructions": "Is this token a wrapped, bridged, staked, leveraged or otherwise derivative version of another asset?",
        "criteria": {
            "true": "The name or symbol indicates a wrapped, bridged, staked, leveraged or derivative version of another asset.",
            "false": "The token appears to be its own standalone asset.",
        },
    },
    "joke_or_shitpost_name": {
        "type": "noul",
        "instructions": "Is the token's name a joke, insult, slang or shitpost with no product meaning?",
        "criteria": {
            "true": "The name is humorous, offensive or meaningless as a product name.",
            "false": "The name reads like a serious product, protocol or brand.",
        },
    },
}


def build_state(name: str | None, symbol: str | None, chain: str | None) -> dict:
    return {
        "token": {"name": name or "", "symbol": symbol or "", "chain": chain or ""},
        "known_projects": KNOWN_PROJECTS,
    }
