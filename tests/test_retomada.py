"""Coleta que sobrevive a queda de rede.

Nesta maquina a rede derruba o processo no meio de chamadas de saida. Sem
checkpoint, uma queda na pagina 7 joga fora as 6 anteriores e a proxima
tentativa recomeca do zero -- com quedas frequentes, a coleta nunca termina.

Cada pagina obtida e gravada antes da proxima, com commit, para aguentar o
processo ser morto sem aviso.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.sources import shopee


class QuedaDeRede(RuntimeError):
    """Simula o processo sendo derrubado no meio da chamada."""


class ClienteFake(shopee.ClienteShopee):
    """Devolve N paginas cheias e falha na pagina configurada.

    Substitui `_post`, nao `executar`: e o ponto onde a resposta ainda vem
    embrulhada em `data`, entao o resto do caminho real (desembrulho, leitura
    de pageInfo, gravacao no cache) continua sendo exercitado.
    """

    def __init__(self, paginas_boas: int, falhar_em: int | None = None,
                 por_pagina: int = 3):
        super().__init__("id", "secret", pausa_s=0, tentativas_max=1)
        self.paginas_boas = paginas_boas
        self.falhar_em = falhar_em
        self.por_pagina = por_pagina
        self.pedidas: list[int] = []

    def _post(self, payload):
        casamento = re.search(r"page: (\\?\d+)", payload)
        pagina = int(casamento.group(1)) if casamento else len(self.pedidas) + 1
        self.pedidas.append(pagina)
        if self.falhar_em is not None and pagina >= self.falhar_em:
            raise QuedaDeRede(f"rede caiu na pagina {pagina}")
        cheia = pagina <= self.paginas_boas
        return {"data": {"productOfferV2": {
            "nodes": [{"itemId": pagina * 100 + i}
                      for i in range(self.por_pagina if cheia else 0)],
            "pageInfo": {"hasNextPage": cheia},
        }}}


class BaseRetomada(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def cache(self, conexao):
        return db.CachePaginas(conexao, "2026-09-04", "shopee")

    def paginar(self, cliente, cache, paginas=5):
        return cliente.paginar(
            lambda pagina: f"query {{ productOfferV2(page: {pagina}) }}",
            3, paginas, 0, "productOfferV2", cache, "teste",
        )


class TestCheckpoint(BaseRetomada):
    def test_grava_cada_pagina_obtida(self):
        cliente = ClienteFake(paginas_boas=3)
        with db.conectar(self.caminho) as conexao:
            self.paginar(cliente, self.cache(conexao))
            guardadas = db.paginas_guardadas(conexao)
        self.assertEqual(guardadas[0]["paginas"], 4)  # 3 cheias + 1 vazia

    def test_retoma_de_onde_parou(self):
        """O cerne: a segunda tentativa nao repete o que ja veio."""
        with db.conectar(self.caminho) as conexao:
            cache = self.cache(conexao)
            primeiro = ClienteFake(paginas_boas=5, falhar_em=3)
            with self.assertRaises(QuedaDeRede):
                self.paginar(primeiro, cache)
            self.assertEqual(primeiro.pedidas, [1, 2, 3])

            segundo = ClienteFake(paginas_boas=5)
            nodes = self.paginar(segundo, cache)
            # Paginas 1 e 2 vieram do checkpoint; so a partir da 3 foi a rede.
            self.assertEqual(segundo.pedidas, [3, 4, 5])
            self.assertEqual(len(nodes), 15)

    def test_conta_quantas_foram_reaproveitadas(self):
        with db.conectar(self.caminho) as conexao:
            cache = self.cache(conexao)
            with self.assertRaises(QuedaDeRede):
                self.paginar(ClienteFake(paginas_boas=5, falhar_em=4), cache)
            cache.reaproveitadas = 0
            self.paginar(ClienteFake(paginas_boas=5), cache)
            self.assertEqual(cache.reaproveitadas, 3)

    def test_sem_cache_sempre_busca_tudo(self):
        cliente = ClienteFake(paginas_boas=5)
        with db.conectar(self.caminho):
            pass
        self.paginar(cliente, None)
        self.assertEqual(cliente.pedidas, [1, 2, 3, 4, 5])

    def test_pagina_incompleta_no_cache_encerra_a_paginacao(self):
        """Pagina menor que o limite significa fim; nao pode pedir a proxima."""
        with db.conectar(self.caminho) as conexao:
            cache = self.cache(conexao)
            self.paginar(ClienteFake(paginas_boas=2), cache)
            segundo = ClienteFake(paginas_boas=2)
            self.paginar(segundo, cache)
        self.assertEqual(segundo.pedidas, [])

    def test_limpar_descarta_o_checkpoint(self):
        with db.conectar(self.caminho) as conexao:
            self.paginar(ClienteFake(paginas_boas=2), self.cache(conexao))
            self.assertEqual(db.limpar_paginas(conexao, "2026-09-04", "shopee"), 3)
            self.assertEqual(db.paginas_guardadas(conexao), [])

    def test_limpar_so_afeta_a_plataforma_pedida(self):
        with db.conectar(self.caminho) as conexao:
            db.CachePaginas(conexao, "2026-09-04", "shopee").guardar("a", 1, [{}])
            db.CachePaginas(conexao, "2026-09-04", "outra").guardar("a", 1, [{}])
            db.limpar_paginas(conexao, "2026-09-04", "shopee")
            restantes = db.paginas_guardadas(conexao)
        self.assertEqual([r["plataforma"] for r in restantes], ["outra"])

    def test_consultas_diferentes_nao_se_misturam(self):
        with db.conectar(self.caminho) as conexao:
            cache = self.cache(conexao)
            cache.guardar("produto:a", 1, [{"itemId": 1}])
            self.assertIsNone(cache.obter("produto:b", 1))
            self.assertEqual(cache.obter("produto:a", 1), [{"itemId": 1}])

    def test_dias_diferentes_nao_se_misturam(self):
        """Reaproveitar pagina de ontem devolveria preco velho como se fosse hoje."""
        with db.conectar(self.caminho) as conexao:
            db.CachePaginas(conexao, "2026-09-03", "shopee").guardar("a", 1, [{}])
            self.assertIsNone(
                db.CachePaginas(conexao, "2026-09-04", "shopee").obter("a", 1))


if __name__ == "__main__":
    unittest.main()
