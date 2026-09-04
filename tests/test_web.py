from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.config import Config, RankingConfig
from flow02.models import Oferta
from flow02.scoring import avaliar
from flow02.config import ScoringConfig
from flow02.web import api, servidor
from flow02.web.tarefas import COMANDOS, Executor, montar_argumentos


def oferta(item_id="1", **kwargs) -> Oferta:
    base = dict(
        plataforma="shopee", item_id=item_id, nome=f"produto {item_id}",
        preco=100.0, taxa_comissao=0.10, origem_consulta="teste",
        vendas=1000, rating=4.5, desconto_pct=20.0, loja_id="loja-a",
        loja_nome="Loja A", link_oferta=f"https://s/{item_id}",
    )
    base.update(kwargs)
    return Oferta(**base)


class TestValidacaoDeArgumentos(unittest.TestCase):
    """A whitelist e a unica coisa entre o front e a execucao de processos."""

    def test_comando_fora_da_whitelist(self):
        with self.assertRaises(ValueError):
            montar_argumentos("rm")

    def test_comando_vazio(self):
        with self.assertRaises(ValueError):
            montar_argumentos("")

    def test_opcao_nao_declarada(self):
        with self.assertRaises(ValueError):
            montar_argumentos("calibrar", {"plataforma": "shopee"})

    def test_plataforma_invalida(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"plataforma": "amazon"})

    def test_dia_invalido(self):
        with self.assertRaises(ValueError):
            montar_argumentos("coletar", {"dia": "2026-09-03; rm -rf /"})

    def test_tipo_invalido(self):
        with self.assertRaises(ValueError):
            montar_argumentos("notificar", {"tipo": "qualquer"})

    def test_tentativa_de_injecao_vira_valor_invalido(self):
        for perigoso in ("shopee && calc", "shopee|whoami", "$(whoami)", "`id`"):
            with self.assertRaises(ValueError):
                montar_argumentos("coletar", {"plataforma": perigoso})

    def test_argumentos_validos(self):
        argv = montar_argumentos(
            "coletar", {"plataforma": "shopee", "dia": "2026-09-03",
                        "com_conversoes": True}
        )
        self.assertEqual(argv[0], "coletar")
        self.assertIn("--plataforma", argv)
        self.assertIn("shopee", argv)
        self.assertIn("--com-conversoes", argv)

    def test_bandeira_falsa_e_omitida(self):
        argv = montar_argumentos("coletar", {"com_conversoes": False})
        self.assertEqual(argv, ["coletar"])

    def test_valor_vazio_e_ignorado(self):
        self.assertEqual(montar_argumentos("coletar", {"plataforma": ""}), ["coletar"])

    def test_dia_hoje_e_aceito(self):
        self.assertIn("hoje", montar_argumentos("coletar", {"dia": "hoje"}))

    def test_todo_comando_tem_rotulo(self):
        for nome, dados in COMANDOS.items():
            self.assertTrue(dados["rotulo"], nome)


class TestExecutor(unittest.TestCase):
    def test_recusa_segunda_tarefa_simultanea(self):
        executor = Executor()
        from flow02.web import tarefas

        executor._tarefa = tarefas.Tarefa("coletar", ["coletar"], "agora")
        with self.assertRaises(RuntimeError):
            executor.iniciar("calibrar")

    def test_cancelar_sem_tarefa_retorna_falso(self):
        self.assertFalse(Executor().cancelar())

    def test_sem_tarefa_atual(self):
        self.assertIsNone(Executor().atual())

    def test_serializacao_da_tarefa(self):
        from flow02.web import tarefas

        tarefa = tarefas.Tarefa("coletar", ["coletar"], "agora")
        tarefa.linhas = ["a", "b", "c"]
        self.assertEqual(tarefa.para_dict()["linhas"], ["a", "b", "c"])
        self.assertEqual(tarefa.para_dict(2)["linhas"], ["c"])
        self.assertEqual(tarefa.para_dict(2)["total_linhas"], 3)


class BaseServidor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        raiz = Path(self._tmp.name)
        self.cfg = Config(
            caminho_banco=raiz / "teste.sqlite3",
            caminho_saida=raiz / "saida",
            ranking=RankingConfig(top=10),
        )
        with db.conectar(self.cfg.caminho_banco) as conexao:
            db.salvar_ofertas(conexao, [
                (o, avaliar(o, ScoringConfig()))
                for o in (oferta("1"), oferta("2", taxa_comissao=0.20))
            ], "2026-09-03")

        self.servidor, self.token = servidor.construir(self.cfg, porta=0)
        self.porta = self.servidor.server_port
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def tearDown(self):
        self.servidor.shutdown()
        self.servidor.server_close()
        self._tmp.cleanup()

    def pedir(self, caminho, token=None, host=None, metodo="GET", corpo=None):
        url = f"http://127.0.0.1:{self.porta}{caminho}"
        cabecalhos = {}
        if token is not None:
            cabecalhos["X-Token"] = token
        if host:
            cabecalhos["Host"] = host
        requisicao = urllib.request.Request(
            url, method=metodo, headers=cabecalhos,
            data=json.dumps(corpo).encode() if corpo is not None else None,
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=10) as resposta:
                return resposta.status, json.loads(resposta.read() or b"{}")
        except urllib.error.HTTPError as exc:
            with exc:
                corpo_erro = exc.read()
            try:
                return exc.code, json.loads(corpo_erro or b"{}")
            except json.JSONDecodeError:
                return exc.code, {}


class TestSeguranca(BaseServidor):
    def test_escuta_so_em_loopback(self):
        self.assertEqual(self.servidor.server_address[0], "127.0.0.1")

    def test_rede_local_e_opt_in(self):
        """O padrao nunca pode expor: o painel executa comandos."""
        outro, _ = servidor.construir(self.cfg, porta=0)
        try:
            self.assertEqual(outro.server_address[0], "127.0.0.1")
        finally:
            outro.server_close()

    def test_com_rede_escuta_em_todas_as_interfaces(self):
        outro, _ = servidor.construir(self.cfg, porta=0, na_rede=True)
        try:
            self.assertEqual(outro.server_address[0], "0.0.0.0")
        finally:
            outro.server_close()

    def test_com_rede_libera_o_ip_da_maquina_no_host(self):
        """Sem isto o celular receberia 403 em tudo, pela defesa de rebinding."""
        outro, _ = servidor.construir(self.cfg, porta=0, na_rede=True)
        try:
            permitidos = outro.RequestHandlerClass.hosts_permitidos
        finally:
            outro.server_close()
        self.assertIn("127.0.0.1", permitidos)
        self.assertGreater(len(permitidos), len(servidor.HOSTS_PERMITIDOS))

    def test_sem_rede_nao_libera_host_extra(self):
        self.assertEqual(
            self.servidor.RequestHandlerClass.hosts_permitidos,
            servidor.HOSTS_PERMITIDOS,
        )

    def test_sem_token_e_rejeitado(self):
        status, _ = self.pedir("/api/estado")
        self.assertEqual(status, 401)

    def test_token_errado_e_rejeitado(self):
        status, _ = self.pedir("/api/estado", token="token-errado")
        self.assertEqual(status, 401)

    def test_token_correto_e_aceito(self):
        status, _ = self.pedir("/api/estado", token=self.token)
        self.assertEqual(status, 200)

    def test_token_tambem_vale_na_query(self):
        status, _ = self.pedir(f"/api/estado?t={self.token}")
        self.assertEqual(status, 200)

    def test_host_externo_e_bloqueado(self):
        """Defesa contra DNS rebinding: so aceita Host de loopback."""
        status, _ = self.pedir("/api/estado", token=self.token, host="site-malicioso.com")
        self.assertEqual(status, 403)

    def test_post_sem_token_e_rejeitado(self):
        status, _ = self.pedir("/api/tarefa", metodo="POST", corpo={"comando": "calibrar"})
        self.assertEqual(status, 401)

    def test_post_com_comando_proibido(self):
        status, dados = self.pedir(
            "/api/tarefa", token=self.token, metodo="POST",
            corpo={"comando": "shutdown"},
        )
        self.assertEqual(status, 400)
        self.assertIn("nao permitido", dados["erro"])

    def test_nao_emite_cabecalhos_cors(self):
        url = f"http://127.0.0.1:{self.porta}/api/estado"
        requisicao = urllib.request.Request(url, headers={"X-Token": self.token})
        with urllib.request.urlopen(requisicao, timeout=10) as resposta:
            self.assertIsNone(resposta.headers.get("Access-Control-Allow-Origin"))

    def test_travessia_de_caminho_e_bloqueada(self):
        status, _ = self.pedir(
            f"/estatico/..%2f..%2f..%2fconfig.toml?t={self.token}"
        )
        self.assertIn(status, (400, 404))

    def test_rota_desconhecida(self):
        status, _ = self.pedir("/api/inexistente", token=self.token)
        self.assertEqual(status, 404)


class TestCarregamentoComoNavegador(BaseServidor):
    """O navegador pede CSS e JS por <link>/<script src>, SEM token.

    Exigir token em /estatico/ passa em teste manual com ?t= na URL, mas
    quebra a pagina inteira no navegador: os arquivos voltam 401 e a tela
    aparece sem estilo nenhum.
    """

    def _sem_token(self, caminho):
        url = f"http://127.0.0.1:{self.porta}{caminho}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resposta:
                return resposta.status, resposta.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            with exc:
                exc.read()
            return exc.code, ""

    def test_css_carrega_sem_token(self):
        status, corpo = self._sem_token("/estatico/estilo.css")
        self.assertEqual(status, 200)
        self.assertIn("--fundo", corpo)

    def test_js_carrega_sem_token(self):
        status, corpo = self._sem_token("/estatico/app.js")
        self.assertEqual(status, 200)
        self.assertIn("carregarRanking", corpo)

    def test_todo_asset_referenciado_no_html_carrega(self):
        """Varre o index.html e busca cada asset como o navegador buscaria."""
        _, html = self._sem_token("/estatico/index.html")
        referencias = re.findall(r'(?:href|src)="(/estatico/[^"]+)"', html)
        self.assertGreaterEqual(len(referencias), 2, "esperava css e js no html")
        for caminho in referencias:
            with self.subTest(asset=caminho):
                status, _ = self._sem_token(caminho)
                self.assertEqual(status, 200, f"{caminho} nao carrega sem token")

    def test_api_continua_exigindo_token(self):
        status, _ = self._sem_token("/api/estado")
        self.assertEqual(status, 401)

    def test_pagina_continua_exigindo_token(self):
        status, _ = self._sem_token("/")
        self.assertEqual(status, 401)


class TestRotas(BaseServidor):
    def test_pagina_inicial(self):
        url = f"http://127.0.0.1:{self.porta}/?t={self.token}"
        with urllib.request.urlopen(url, timeout=10) as resposta:
            corpo = resposta.read().decode()
        self.assertEqual(resposta.status, 200)
        self.assertIn("<title>flow02</title>", corpo)

    def test_estado(self):
        _, dados = self.pedir("/api/estado", token=self.token)
        self.assertEqual(dados["total_ofertas"], 2)
        self.assertIn("2026-09-03", dados["dias"])
        self.assertIn("shopee", dados["integracoes"])

    def test_top_ordenado_por_epc(self):
        _, dados = self.pedir("/api/top?dia=2026-09-03", token=self.token)
        self.assertEqual(dados["total"], 2)
        self.assertEqual(dados["itens"][0]["item_id"], "2")
        self.assertEqual(dados["itens"][0]["posicao"], 1)

    def test_top_respeita_limite(self):
        _, dados = self.pedir("/api/top?dia=2026-09-03&limite=1", token=self.token)
        self.assertEqual(len(dados["itens"]), 1)

    def test_top_filtra_por_busca(self):
        _, dados = self.pedir(
            "/api/top?dia=2026-09-03&busca=produto%202", token=self.token
        )
        self.assertEqual([i["item_id"] for i in dados["itens"]], ["2"])

    def test_top_de_dia_vazio(self):
        _, dados = self.pedir("/api/top?dia=1999-01-01", token=self.token)
        self.assertEqual(dados["itens"], [])

    def test_alertas_sem_dia_anterior(self):
        _, dados = self.pedir("/api/alertas?dia=2026-09-03", token=self.token)
        self.assertIsNone(dados["anterior"])
        self.assertEqual(dados["itens"], [])

    def test_ganhos_vazio(self):
        _, dados = self.pedir("/api/ganhos", token=self.token)
        self.assertEqual(dados["totais"]["comissao"], 0)

    def test_tarefa_sem_execucao(self):
        _, dados = self.pedir("/api/tarefa", token=self.token)
        self.assertIsNone(dados["tarefa"])
        self.assertIn("coletar", dados["comandos"])


class TestApiDireta(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(caminho_banco=Path(self._tmp.name) / "t.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def test_dia_invalido_cai_para_hoje(self):
        resultado = api.top(self.cfg, {"dia": "nao-e-data"})
        self.assertRegex(resultado["dia"], r"^\d{4}-\d{2}-\d{2}$")

    def test_plataforma_invalida_e_ignorada(self):
        resultado = api.top(self.cfg, {"plataforma": "amazon"})
        self.assertEqual(resultado["itens"], [])

    def test_limite_absurdo_e_limitado(self):
        with db.conectar(self.cfg.caminho_banco) as conexao:
            db.salvar_ofertas(conexao, [
                (oferta(str(i)), avaliar(oferta(str(i)), ScoringConfig()))
                for i in range(3)
            ], api._dia(None))
        resultado = api.top(self.cfg, {"limite": "999999"})
        self.assertLessEqual(len(resultado["itens"]), 500)

    def test_limite_nao_numerico_usa_padrao(self):
        self.assertEqual(api._inteiro("abc", 7), 7)


if __name__ == "__main__":
    unittest.main()
