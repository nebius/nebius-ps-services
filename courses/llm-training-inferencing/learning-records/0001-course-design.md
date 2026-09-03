# Course design record

Decision: use a hand-sized decoder to expose mechanics, then a small public model to expose production libraries. Training and serving dependencies remain separate. Every performance exercise states which state is replicated, sharded, recomputed, cached, or communicated, and no target-cluster result is fabricated.
