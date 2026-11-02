![SpeedPanel — sua conexão, em perspectiva](static/banner.svg)

# SpeedPanel

Painel privado para executar e comparar medições de internet a partir do servidor.

## Executar com Docker

```sh
export ADMIN_PASSWORD='defina-uma-senha-forte'
export SESSION_SECRET="$(openssl rand -hex 32)"
docker compose up -d --build
```

Abra `http://localhost:8095`. Os resultados ficam no volume persistente em `/data`.
Não publique o painel na internet sem HTTPS e controles de acesso.

## Configuração

Em **Serviços e regras**, configure cada serviço, execute um teste manual e ative os testes automáticos. Cada serviço aceita múltiplas expressões cron separadas por ponto e vírgula. O fuso inicial é America/Sao_Paulo; pode ser alterado em Configurações. Os agendamentos começam desativados para revisão pelo administrador.

Defina limites de download, upload, latência, jitter e perda. Zero desativa o limite. As violações ficam destacadas no histórico; este aplicativo não envia notificações externas. Métricas não fornecidas pelo serviço aparecem como indisponíveis, sem estimativas.

Os testes são executados em sequência, com fila persistente. Um teste que atravessa um horário não impede a criação dos próximos trabalhos. Trabalhos já enfileirados preservam a configuração existente na criação. Horários ocorridos com o servidor desligado não são recuperados. Testes interrompidos por reinício aparecem como falhas.

## Integrações

**Ookla:** usa o executável oficial Speedtest CLI 1.2.0, com suporte a ID de servidor e interface do contêiner. A execução usa os parâmetros de aceite da licença e GDPR do CLI. Termos: https://www.speedtest.net/about/terms. O binário Docker incluído é para x86_64.

**Fast.com:** abre o site em Chromium e coleta download, upload e latência. Jitter e perda não são fornecidos por esse adaptador.

**Brasil Banda Larga:** abre o site em Chromium, inicia a medição e lê o resultado salvo pelo próprio site. Coleta download, upload, latência, jitter e perda quando disponibilizados. Automação de sites depende de sua estrutura e pode precisar de ajustes se ela mudar.

**Site personalizado:** informe URL, seletores CSS para clicar em ordem, seletor de conclusão e seletores de métricas. Exemplo: cliques `["#iniciar"]`, conclusão `#resultado.concluido`, métricas `{"download":"#download","upload":"#upload","ping":"#ping"}`. A conclusão deve ficar visível apenas depois de terminar o teste. Valores precisam estar em Mbps, ms e %. Não é executado JavaScript fornecido pelo usuário.

**API JSON:** importa medições existentes de um endpoint GET. Não transforma um endpoint genérico em um teste de velocidade. Exemplo de caminhos: `{"download":"result.download","upload":"result.upload","ping":"result.ping"}`. Valores precisam estar em Mbps, ms e %. Para uma API que dispara testes com autenticação ou fluxo próprio, é necessário desenvolver um adaptador específico.

Cada novo serviço exige uma integração compatível; cadastrar apenas a URL não garante que o site possa ser automatizado. Não há promessa de suporte universal.

## Histórico e armazenamento

Filtros por serviço e intervalo; gráficos de download, upload, latência, jitter e perda; tabela paginada; exportação CSV, relatório PDF e Excel (.xlsx). A tela mostra até 10.000 registros por filtro; os arquivos incluem todo o intervalo, sem esse corte. O PDF contém resumo, comparativo por serviço, gráficos de download/upload, histórico e detalhes das falhas/regras. O Excel contém as abas Resumo e Medições, com datas e números tipados, filtros e gráfico comparativo. Ambos respeitam os campos de período e serviço do painel, usam o fuso configurado e são fotografias dos resultados no momento da geração. Métricas ausentes ficam vazias no Excel e marcadas com hífen no PDF.

Banco SQLite no volume Docker persistente montado em /data. Retenção inicial de 365 dias, configurável. Dados brutos e configuração usada ficam armazenados junto aos resultados.

Os horários da tela usam o fuso configurado; os filtros de data usam o fuso do navegador. CSV em UTC. Métricas agregadas excluem falhas e trabalhos pendentes. Todos os testes medem a conexão do servidor, não a conexão do dispositivo que abre o painel.

## Acesso e manutenção

A senha inicial fica na variável `ADMIN_PASSWORD` (mapeada de `SERVICE_PASSWORD_ADMIN` no Coolify) e é usada apenas para criar o acesso inicial. Depois, altere a senha pelo painel. A variável `SESSION_SECRET` (mapeada de `SERVICE_PASSWORD_64_SECRET` no Coolify) assina as sessões e deve ser preservada.

O serviço pode ser construído pelo Docker Compose ou publicado no Coolify. Preserve as variáveis secretas e faça backup do volume antes de migrar.

Para backup consistente com o serviço rodando, use a API de backup do SQLite ou pare o serviço durante a cópia do volume. Um arquivo SQLite copiado durante escrita pode ficar inconsistente.

O Compose fornecido vincula o painel a localhost na porta 8095. Para acesso público, configure domínio e HTTPS no proxy do Coolify antes de publicar.

## Verificação

`python test_app.py` verifica autenticação, CSRF, validação de agendamentos, regras, persistência, preservação do histórico ao excluir serviço e escape de fórmulas no CSV. A validação real de cada integração deve executar uma medição no servidor e conferir o histórico.

## Diagnóstico de medições

A coluna **Detalhes / regras** distingue falhas de execução e resultados que não atendem aos limites configurados.

- `ConfigurationError` / `Cannot open socket` (Ookla): verifique conectividade, DNS e acesso aos servidores de medição. Teste sem ID de servidor ou interface fixa.
- `ERR_INTERNET_DISCONNECTED`: o navegador do teste não tem conectividade; confira a rede Docker e a rota de saída.
- `ERR_NAME_NOT_RESOLVED`: falha de resolução DNS no container.
- `Tempo limite`: o serviço não terminou no prazo; confirme a disponibilidade antes de aumentar o timeout.
- `Revisar regras`: a medição terminou, mas alguma métrica está fora dos limites ou não está disponível nesse adaptador.

O endpoint `/health` verifica a aplicação e o worker; não comprova acesso à internet.
Uma rede Docker removida deve ser restaurada antes de recriar o container pela configuração do deployment. Preserve o volume de dados.
Erros históricos permanecem como registro; uma nova medição bem-sucedida não altera o histórico.

## Identidade visual

Logo e favicon: [`static/logo.svg`](static/logo.svg). Banner: [`static/banner.svg`](static/banner.svg).
Os arquivos vetoriais compartilham a paleta escura, verde menta e azul da interface.

Para rodar os testes de relatórios, instale também as dependências de desenvolvimento: `pip install -r requirements-dev.txt`.

