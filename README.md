# 📍 Alocador de Agendas e Roteirizador H3 - Rio de Janeiro

Aplicação interativa desenvolvida em **Streamlit** para agendamento de atendimentos domiciliares (vacinação) e roteirização otimizada de veículos de saúde na cidade do Rio de Janeiro.

## 🚀 Funcionalidades
- **Indexação Espacial H3**: Discretização do território municipal em malha hexagonal Uber H3 (Resolução 9).
- **Roteamento A\***: Cálculo de menor caminho e estimativa de tempo considerando vias expressas congestionadas e multiplicadores de pico horário.
- **Zonas de Exclusão**: Mapeamento de vazios geográficos e áreas sem atendimento com ferramenta interativa de desenho/laço.
- **Alocação Viável de Slots**: Verificação em tempo real de viabilidade temporal e deslocamento da frota.

## 🛠️ Como Executar
1. Instale as dependências:
   ```bash
   pip install -r requirements.txt