import asyncio
import random
from k2_optimus.schemas import MarketState, MarketRegime
from k2_optimus.agents import ShortInterestAgentPair
from k2_optimus.connectors import ShortsConnector
from k2_optimus.memory import DomainBrain, PostMortemAgent

async def run_simulation():
    print("Initializing ChromaDB Domain Brain...")
    brain = DomainBrain(db_path="./.chroma_db")
    post_mortem = PostMortemAgent(brain=brain)
    
    # Randomly force high SIR 50% of the time to generate signals
    connector = ShortsConnector(force_high_sir=random.choice([True, False]))
    agent_pair = ShortInterestAgentPair(connector=connector)
    
    print("\n--- Starting 50-Trade Simulation ---\n")
    
    for i in range(1, 51):
        # 1. Randomize Macro Constraints
        credit_stress = round(random.uniform(0.8, 1.4), 2)
        regime = MarketRegime.BULL_NORMAL if credit_stress <= 1.1 else MarketRegime.BEAR_STRESS
        
        market_state = MarketState(
            credit_stress_index=credit_stress,
            market_regime=regime
        )
        
        # 2. Run the Swarm Loop
        connector.force_high_sir = random.choice([True, False]) # Randomize data intake per run
        result = await agent_pair.run(market_state=market_state)
        
        # 3. Randomize Real-World Outcome
        # In reality, this relies on a separate process verifying the signal vs the market T+1.
        if result.trading_signal == 0:
            outcome = "Neutral / No Trade"
        else:
            outcome_prob = random.random()
            if result.trading_signal == 1:
                # Assuming Bullish in Bull Market has higher success
                success_chance = 0.7 if regime == MarketRegime.BULL_NORMAL else 0.3
                outcome = "Success" if outcome_prob < success_chance else "Failure"
            else:
                outcome = "Success" if outcome_prob < 0.5 else "Failure"
                
        # 4. Agent Post-Mortem execution
        doc_id = post_mortem.extract_and_store_lesson(
            result=result, 
            market_state=market_state, 
            trade_outcome=outcome
        )
        
        print(f"[{i:02d}] Regime: {regime.value:15} | Signal: {result.trading_signal:2} | Verify: {result.status.value:8} | Outcome: {outcome:20} -> Memory ID: {doc_id[-6:]}")
        
    print(f"\nSimulation complete. {brain.collection.count()} total lessons now in Vector Memory.")

if __name__ == "__main__":
    asyncio.run(run_simulation())
