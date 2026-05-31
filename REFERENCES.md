# Referências de estilo para o carrossel

Posts de Instagram que o cliente quer usar como **referência visual/estilo** na
produção dos carrosséis (layout, ritmo, uso de cor, tipografia, formato):

- https://www.instagram.com/p/DYzDm-nDEZR/?img_index=6
- https://www.instagram.com/p/DY7kr1jjmxD/?img_index=1
- https://www.instagram.com/p/DYx5NDAGrnw/?img_index=1
- https://www.instagram.com/p/DY5asyljrn1/?hl=pt-br&img_index=1

## Como entram no sistema (importante)
Não há API pública para baixar posts arbitrários do Instagram (sem Meta), e
raspar o IG é bloqueado/contra os Termos. Então as referências entram por
**upload**: o usuário baixa as imagens desses posts e sobe como "referência".

## Plano de uso (quando o carrossel for retomado)
1. Upload das imagens de referência (flag `referencia: true`).
2. A VISÃO (gpt-4o) descreve o ESTILO de cada uma: estrutura (capa/miolo/CTA),
   uso de cor, tipografia, densidade de texto, formato (lista, citação, etc.).
3. Esse "perfil de estilo" é guardado no brand book.
4. O gerador de carrossel consome o estilo + os valores de marca confirmados
   para produzir slides na mesma linguagem das referências.
