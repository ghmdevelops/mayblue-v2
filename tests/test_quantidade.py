from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02.cli import _ajustar_quantidade
from flow02.config import ColetaConfig, Config, ConsultaConfig
from flow02.web.tarefas import montar_argumentos


def config(paginas=3, limite=50) -> Config:
    return Config(coleta=ColetaConfig(
        limite_por_pagina=limite,
        campanhas_paginas=2,
        consultas=(
            ConsultaConfig(nome="a", paginas=paginas),
            ConsultaConfig(nome="b", paginas=paginas),
        ),
    ))


class TestAjusteDeQuantidade(unittest.TestCase):
    def test_sem_argumentos_nao_muda_nada(self):
        cfg = config()
        self.assertIs(_ajustar_quantidade(cfg, None, None), cfg)

    def test_converte_quantidade_em_paginas(self):
        """200 produtos com página de 50 = 4 páginas por consulta."""
        cfg = _ajustar_quantidade(config(), 200, None)
        self.assertEqual([c.paginas for c in cfg.coleta.consultas], [4, 4])

    def test_arredonda_para_cima(self):
        cfg = _ajustar_quantidade(config(), 120, None)
        self.assertEqual(cfg.coleta.consultas[0].paginas, 3)

    def test_quantidade_menor_que_uma_pagina(self):
        cfg = _ajustar_quantidade(config(), 10, None)
        self.assertEqual(cfg.coleta.consultas[0].paginas, 1)

    def test_limite_customizado_muda_a_conta(self):
        cfg = _ajustar_quantidade(config(), 200, 20)
        self.assertEqual(cfg.coleta.limite_por_pagina, 20)
        self.assertEqual(cfg.coleta.consultas[0].paginas, 10)

    def test_limite_e_limitado_a_100(self):
        cfg = _ajustar_quantidade(config(), None, 9999)
        self.assertEqual(cfg.coleta.limite_por_pagina, 100)

    def test_so_limite_nao_mexe_nas_paginas(self):
        cfg = _ajustar_quantidade(config(paginas=7), None, 25)
        self.assertEqual(cfg.coleta.limite_por_pagina, 25)
        self.assertEqual(cfg.coleta.consultas[0].paginas, 7)

    def test_campanhas_nao_passam_do_pedido(self):
        cfg = _ajustar_quantidade(config(), 10, None)
        self.assertEqual(cfg.coleta.campanhas_paginas, 1)

    def test_config_original_nao_e_mutado(self):
        original = config()
        _ajustar_quantidade(original, 500, None)
        self.assertEqual(original.coleta.consultas[0].paginas, 3)


class TestQuantidadeNoPainel(unittest.TestCase):
    def test_valor_valido_vira_argumento(self):
        argv = montar_argumentos("coletar", {"quantidade": "200"})
        self.assertEqual(argv, ["coletar", "--quantidade", "200"])

    def test_aceita_inteiro(self):
        self.assertIn("200", montar_argumentos("coletar", {"quantidade": 200}))

    def test_vazio_e_ignorado(self):
        self.assertEqual(montar_argumentos("coletar", {"quantidade": ""}), ["coletar"])

    def test_texto_e_rejeitado(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"quantidade": "muitos"})

    def test_injecao_e_rejeitada(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"quantidade": "50; rm -rf /"})

    def test_zero_e_rejeitado(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"quantidade": "0"})

    def test_valor_absurdo_e_rejeitado(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"quantidade": "999999"})

    def test_negativo_e_rejeitado(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"quantidade": "-5"})


if __name__ == "__main__":
    unittest.main()
