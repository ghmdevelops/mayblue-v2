"use strict";

/*
  Escritor de ZIP mínimo, método STORE (sem compressão).

  Por que escrever à mão: baixar 20 PNGs soltos faz o navegador bloquear
  ("este site quer baixar vários arquivos"), e não há JSZip disponível --
  o registry do npm está bloqueado nesta rede, igual ao PyPI.

  STORE em vez de DEFLATE porque PNG já é comprimido: a economia seria
  desprezível e exigiria implementar deflate à mão.

  Referência do formato: APPNOTE.TXT da PKWARE, seções 4.3.7 (local header),
  4.3.12 (central directory) e 4.3.16 (end of central directory).
*/

const ZIP = (() => {
  const TABELA_CRC = (() => {
    const tabela = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let valor = i;
      for (let bit = 0; bit < 8; bit++) {
        valor = valor & 1 ? 0xEDB88320 ^ (valor >>> 1) : valor >>> 1;
      }
      tabela[i] = valor >>> 0;
    }
    return tabela;
  })();

  function crc32(bytes) {
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) {
      crc = TABELA_CRC[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8);
    }
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  /** Data/hora no formato MS-DOS, que é o que o ZIP usa. */
  function dataDos(quando) {
    const data = ((quando.getFullYear() - 1980) << 9)
      | ((quando.getMonth() + 1) << 5) | quando.getDate();
    const hora = (quando.getHours() << 11) | (quando.getMinutes() << 5)
      | (quando.getSeconds() >> 1);
    return { data, hora };
  }

  function escrever(visao, deslocamento, valores) {
    let posicao = deslocamento;
    for (const [tamanho, valor] of valores) {
      if (tamanho === 2) visao.setUint16(posicao, valor, true);
      else visao.setUint32(posicao, valor, true);
      posicao += tamanho;
    }
    return posicao;
  }

  /**
   * @param {{nome: string, dados: Uint8Array}[]} arquivos
   * @returns {Blob}
   */
  function criar(arquivos) {
    const codificador = new TextEncoder();
    const { data, hora } = dataDos(new Date());

    const entradas = arquivos.map((arquivo) => ({
      nomeBytes: codificador.encode(arquivo.nome),
      dados: arquivo.dados,
      crc: crc32(arquivo.dados),
    }));

    const tamanhoLocal = entradas.reduce(
      (soma, e) => soma + 30 + e.nomeBytes.length + e.dados.length, 0);
    const tamanhoCentral = entradas.reduce(
      (soma, e) => soma + 46 + e.nomeBytes.length, 0);

    const buffer = new ArrayBuffer(tamanhoLocal + tamanhoCentral + 22);
    const bytes = new Uint8Array(buffer);
    const visao = new DataView(buffer);

    let posicao = 0;
    for (const entrada of entradas) {
      entrada.deslocamento = posicao;
      posicao = escrever(visao, posicao, [
        [4, 0x04034b50], [2, 20], [2, 0], [2, 0], [2, hora], [2, data],
        [4, entrada.crc], [4, entrada.dados.length], [4, entrada.dados.length],
        [2, entrada.nomeBytes.length], [2, 0],
      ]);
      bytes.set(entrada.nomeBytes, posicao);
      posicao += entrada.nomeBytes.length;
      bytes.set(entrada.dados, posicao);
      posicao += entrada.dados.length;
    }

    const inicioCentral = posicao;
    for (const entrada of entradas) {
      posicao = escrever(visao, posicao, [
        [4, 0x02014b50], [2, 20], [2, 20], [2, 0], [2, 0], [2, hora], [2, data],
        [4, entrada.crc], [4, entrada.dados.length], [4, entrada.dados.length],
        [2, entrada.nomeBytes.length], [2, 0], [2, 0], [2, 0], [2, 0], [4, 0],
        [4, entrada.deslocamento],
      ]);
      bytes.set(entrada.nomeBytes, posicao);
      posicao += entrada.nomeBytes.length;
    }

    escrever(visao, posicao, [
      [4, 0x06054b50], [2, 0], [2, 0],
      [2, entradas.length], [2, entradas.length],
      [4, posicao - inicioCentral], [4, inicioCentral], [2, 0],
    ]);

    return new Blob([buffer], { type: "application/zip" });
  }

  function baixar(blob, nome) {
    const url = URL.createObjectURL(blob);
    const ancora = document.createElement("a");
    ancora.href = url;
    ancora.download = nome;
    ancora.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return { criar, baixar, crc32 };
})();
