"""Carregamento de configuracao (config.toml) e segredos (.env)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CAMINHO_CONFIG = RAIZ / "config.toml"
CAMINHO_ENV = RAIZ / ".env"


class ConfigInvalida(RuntimeError):
    pass


def resolver(caminho: str | Path) -> Path:
    """Resolve caminhos relativos contra a raiz do projeto, nao contra o CWD.

    Necessario porque o agendador do Windows executa a tarefa com CWD arbitrario.
    """
    caminho = Path(caminho)
    return caminho if caminho.is_absolute() else RAIZ / caminho


@dataclass(frozen=True, slots=True)
class ConsultaConfig:
    nome: str
    sort_type: int = 5
    list_type: int | None = None
    match_id: int | None = None
    keyword: str | None = None
    product_cat_id: int | None = None
    is_ams_offer: bool | None = None
    # Vendedores selecionados pela Shopee: catalogo menor, entrega mais
    # confiavel. Util para quem prefere indicar com menos risco de reclamacao.
    is_key_seller: bool | None = None
    paginas: int = 2


@dataclass(frozen=True, slots=True)
class VarreduraConfig:
    """Varredura por categoria.

    As consultas globais devolvem o ranking geral da Shopee, que e estreito e
    enviesado para as categorias grandes. Cada categoria tem o proprio topo de
    comissao. Rode `flow02 categorias` para descobrir os IDs reais que
    aparecem na sua coleta, em vez de adivinhar IDs de catalogo.
    """

    habilitada: bool = False
    categorias: tuple[int, ...] = ()
    paginas_por_categoria: int = 2
    sort_type: int = 5
    requisicoes_max: int = 200
    # Listas curadas da Shopee para a mesma categoria. Medido em 2026-09-04:
    # listType 3 e 4 com matchId devolvem conjuntos diferentes entre si e
    # diferentes do productCatId -- cada um e uma vitrine propria.
    list_types: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ColetaConfig:
    limite_por_pagina: int = 50
    pausa_entre_requisicoes_s: float = 1.0
    tentativas_max: int = 4
    timeout_s: float = 30.0
    coletar_campanhas: bool = True
    campanhas_paginas: int = 2
    conversoes_paginas: int = 10
    validation_ids: tuple[int, ...] = ()
    varredura: VarreduraConfig = field(default_factory=VarreduraConfig)
    campos_produto: tuple[str, ...] = ()
    campos_campanha: tuple[str, ...] = ()
    campos_conversao: tuple[str, ...] = ()
    campos_validado: tuple[str, ...] = ()
    consultas: tuple[ConsultaConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class FiltrosConfig:
    taxa_comissao_min: float = 0.0
    preco_min: float = 0.0
    preco_max: float = float("inf")
    vendas_min: int = 0
    rating_min: float = 0.0
    exigir_link_oferta: bool = True


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    cvr_base: float = 0.02
    cvr_maximo: float = 0.5
    vendas_referencia: int = 5000
    peso_desconto: float = 0.6
    preco_referencia: float = 150.0
    elasticidade_preco: float = 0.5
    popularidade_min: float = 0.25
    popularidade_max: float = 2.0
    confianca_sem_rating: float = 0.6


@dataclass(frozen=True, slots=True)
class RankingConfig:
    top: int = 20
    max_por_loja: int = 3
    dias_minimos_historico: int = 2


@dataclass(frozen=True, slots=True)
class AlertasConfig:
    """Limiares do detector de mudanca entre snapshots diarios."""

    variacao_comissao_min: float = 0.20
    variacao_preco_min: float = 0.10
    top_referencia: int = 20
    dias_para_sumico: int = 1


@dataclass(frozen=True, slots=True)
class PrecoConfig:
    """Deteccao de desconto inflado.

    O priceDiscountRate da Shopee e contra o "preco original" declarado pelo
    vendedor, que costuma ser inflado. Com snapshots diarios da para comparar
    com a mediana realmente praticada.
    """

    janela_dias: int = 30
    dias_minimos: int = 5
    tolerancia_pp: float = 15.0


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    habilitado: bool = False
    chat_id: str = ""
    top: int = 10
    token: str | None = None

    @property
    def configurado(self) -> bool:
        return bool(self.habilitado and self.chat_id and self.token)


@dataclass(frozen=True, slots=True)
class CalibracaoConfig:
    """Troca o CVR heuristico pelo CVR medido, quando houver amostra.

    `cliques_minimos` pode ser baixo porque o encolhimento bayesiano ja
    protege contra amostra pequena -- ele serve so para nao encher a tabela
    de itens com 1 ou 2 cliques.
    """

    habilitada: bool = True
    cliques_minimos: int = 20
    janela_dias: int = 30
    peso_prior: float = 150.0
    cvr_prior_padrao: float = 0.02


@dataclass(frozen=True, slots=True)
class MercadoLivreConfig:
    """O ML nao tem API de afiliados; ver sources/mercadolivre.py."""

    caminho_curadoria: str = "entrada/mercadolivre.csv"
    caminho_urls: str = "entrada/mercadolivre_urls.txt"
    taxa_comissao_padrao: float = 0.0
    comissao_por_categoria: dict[str, float] = field(default_factory=dict)
    access_token: str | None = None


@dataclass(frozen=True, slots=True)
class FirebaseConfig:
    """Espelha o resultado no Realtime Database (opcional).

    O SQLite continua sendo a fonte de verdade; o Firebase e so a vitrine
    para consumir de celular, web ou automacao.
    """

    habilitado: bool = False
    database_url: str = ""
    projeto: str = ""
    regiao: str = ""
    raiz: str = "flow02"
    enviar_ganhos: bool = True
    api_key: str | None = None
    email: str | None = None
    senha: str | None = None
    segredo: str | None = None

    @property
    def url_inferida(self) -> bool:
        """A URL veio do projectId em vez de ter sido informada."""
        return not self.database_url and bool(self.projeto)

    @property
    def url(self) -> str:
        """URL do Realtime Database, inferida do projeto se nao informada.

        O snippet de config que o console gera so traz `databaseURL` se o
        Realtime Database ja existir no projeto -- e um esquecimento comum.
        Inferir a partir do projectId cobre o caso padrao (regiao us-central1,
        dominio firebaseio.com); outras regioes usam firebasedatabase.app e
        precisam de `regiao` no config.
        """
        if self.database_url:
            return self.database_url.rstrip("/")
        if not self.projeto:
            return ""
        if self.regiao and self.regiao != "us-central1":
            return f"https://{self.projeto}-default-rtdb.{self.regiao}.firebasedatabase.app"
        return f"https://{self.projeto}-default-rtdb.firebaseio.com"

    def urls_candidatas(self) -> tuple[str, ...]:
        """Todas as URLs plausiveis, para o `doctor` descobrir qual existe."""
        if self.database_url:
            return (self.database_url.rstrip("/"),)
        if not self.projeto:
            return ()
        base = f"{self.projeto}-default-rtdb"
        regioes = ("us-central1", "southamerica-east1", "europe-west1",
                   "asia-southeast1")
        return (f"https://{base}.firebaseio.com", *(
            f"https://{base}.{regiao}.firebasedatabase.app" for regiao in regioes
        ))

    @property
    def configurado(self) -> bool:
        return bool(self.habilitado and self.url)


@dataclass(frozen=True, slots=True)
class Credenciais:
    shopee_app_id: str | None = None
    shopee_app_secret: str | None = None

    @property
    def shopee_ok(self) -> bool:
        return bool(self.shopee_app_id and self.shopee_app_secret)


@dataclass(frozen=True, slots=True)
class Config:
    coleta: ColetaConfig = field(default_factory=ColetaConfig)
    filtros: FiltrosConfig = field(default_factory=FiltrosConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    calibracao: CalibracaoConfig = field(default_factory=CalibracaoConfig)
    alertas: AlertasConfig = field(default_factory=AlertasConfig)
    preco: PrecoConfig = field(default_factory=PrecoConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    firebase: FirebaseConfig = field(default_factory=FirebaseConfig)
    mercadolivre: MercadoLivreConfig = field(default_factory=MercadoLivreConfig)
    credenciais: Credenciais = field(default_factory=Credenciais)
    caminho_banco: Path = RAIZ / "dados" / "flow02.sqlite3"
    caminho_saida: Path = RAIZ / "saida"


def carregar_env(caminho: Path = CAMINHO_ENV) -> dict[str, str]:
    """Le um .env simples (CHAVE=valor) sem sobrescrever o ambiente real."""
    valores: dict[str, str] = {}
    if caminho.exists():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            valores[chave.strip()] = valor.strip().strip("'\"")
    for chave, valor in valores.items():
        os.environ.setdefault(chave, valor)
    return valores


def _consultas(bruto: list[dict]) -> tuple[ConsultaConfig, ...]:
    permitidos = set(ConsultaConfig.__slots__)
    consultas = []
    for item in bruto:
        desconhecidos = set(item) - permitidos
        if desconhecidos:
            raise ConfigInvalida(
                f"consulta '{item.get('nome', '?')}' tem chaves invalidas: "
                f"{sorted(desconhecidos)}"
            )
        if "nome" not in item:
            raise ConfigInvalida("toda consulta precisa de 'nome'")
        consultas.append(ConsultaConfig(**item))
    return tuple(consultas)


def _secao(bruto: dict, chave: str, classe, **extra):
    dados = dict(bruto.get(chave, {}))
    dados.update(extra)
    permitidos = set(classe.__slots__)
    desconhecidos = set(dados) - permitidos
    if desconhecidos:
        raise ConfigInvalida(f"secao [{chave}] tem chaves invalidas: {sorted(desconhecidos)}")
    return classe(**dados)


def carregar(caminho: Path = CAMINHO_CONFIG) -> Config:
    carregar_env()
    bruto: dict = {}
    if caminho.exists():
        bruto = tomllib.loads(caminho.read_text(encoding="utf-8"))

    coleta_bruta = dict(bruto.get("coleta", {}))
    consultas = _consultas(coleta_bruta.pop("consultas", []))
    campos = {
        nome: tuple(coleta_bruta.pop(nome, []))
        for nome in ("campos_produto", "campos_campanha",
                     "campos_conversao", "campos_validado")
    }
    coleta_bruta["validation_ids"] = tuple(coleta_bruta.get("validation_ids", []))
    varredura_bruta = dict(coleta_bruta.pop("varredura", {}))
    varredura_bruta["categorias"] = tuple(varredura_bruta.get("categorias", []))
    varredura_bruta["list_types"] = tuple(varredura_bruta.get("list_types", []))
    coleta = _secao(
        {"coleta": coleta_bruta}, "coleta", ColetaConfig,
        consultas=consultas,
        varredura=_secao({"v": varredura_bruta}, "v", VarreduraConfig),
        **campos,
    )

    filtros_brutos = dict(bruto.get("filtros", {}))
    if filtros_brutos.get("preco_max") in (0, None):
        filtros_brutos.pop("preco_max", None)

    geral = bruto.get("geral", {})
    ml_bruto = dict(bruto.get("mercadolivre", {}))
    ml_bruto["access_token"] = os.environ.get("ML_ACCESS_TOKEN") or None
    ml_bruto["comissao_por_categoria"] = {
        str(chave).lower(): float(valor)
        for chave, valor in (ml_bruto.pop("comissao_por_categoria", {}) or {}).items()
    }

    fb_bruto = dict(bruto.get("firebase", {}))
    fb_bruto.update(
        api_key=os.environ.get("FIREBASE_API_KEY") or None,
        email=os.environ.get("FIREBASE_EMAIL") or None,
        senha=os.environ.get("FIREBASE_SENHA") or None,
        segredo=os.environ.get("FIREBASE_DB_SECRET") or None,
    )
    for variavel, chave in (("FIREBASE_DATABASE_URL", "database_url"),
                            ("FIREBASE_PROJETO", "projeto")):
        if os.environ.get(variavel):
            fb_bruto[chave] = os.environ[variavel]

    tg_bruto = dict(bruto.get("telegram", {}))
    tg_bruto["token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or None
    if os.environ.get("TELEGRAM_CHAT_ID"):
        tg_bruto["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]

    return Config(
        coleta=coleta,
        filtros=_secao({"filtros": filtros_brutos}, "filtros", FiltrosConfig),
        scoring=_secao(bruto, "scoring", ScoringConfig),
        ranking=_secao(bruto, "ranking", RankingConfig),
        calibracao=_secao(bruto, "calibracao", CalibracaoConfig),
        alertas=_secao(bruto, "alertas", AlertasConfig),
        preco=_secao(bruto, "preco", PrecoConfig),
        telegram=_secao({"telegram": tg_bruto}, "telegram", TelegramConfig),
        firebase=_secao({"firebase": fb_bruto}, "firebase", FirebaseConfig),
        mercadolivre=_secao({"mercadolivre": ml_bruto}, "mercadolivre", MercadoLivreConfig),
        credenciais=Credenciais(
            shopee_app_id=os.environ.get("SHOPEE_APP_ID") or None,
            shopee_app_secret=os.environ.get("SHOPEE_APP_SECRET") or None,
        ),
        caminho_banco=resolver(geral.get("caminho_banco", "dados/flow02.sqlite3")),
        caminho_saida=resolver(geral.get("caminho_saida", "saida")),
    )
