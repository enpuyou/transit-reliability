# ruff: noqa: E501, E701, E702, E731, F841
"""Databricks capability check (decision: docs/ml_databricks.md). Run as a one-off job, not a normal script."""

import json
import time

out = {}
t = time.time()
try:
    import torch

    out["torch"] = torch.__version__
    out["cuda"] = torch.cuda.is_available()
    out["threads"] = torch.get_num_threads()
    out["import_seconds"] = round(time.time() - t, 1)
    # a small BusTr/Dublin-style network: embeddings + MLP, and a small LSTM, on 500k and 200k synthetic rows
    torch.manual_seed(0)
    n = 500_000
    route = torch.randint(0, 200, (n,))
    how = torch.randint(0, 168, (n,))
    num = torch.randn(n, 12)
    y = num[:, 0] * 3 + torch.sin(num[:, 1]) + torch.randn(n) * 0.5

    class Net(torch.nn.Module):
        def __init__(s):
            super().__init__()
            s.r = torch.nn.Embedding(200, 8)
            s.h = torch.nn.Embedding(168, 8)
            s.f = torch.nn.Sequential(
                torch.nn.Linear(28, 128),
                torch.nn.ReLU(),
                torch.nn.Linear(128, 64),
                torch.nn.ReLU(),
                torch.nn.Linear(64, 1),
            )

        def forward(s, r, h, x):
            return s.f(torch.cat([s.r(r), s.h(h), x], 1)).squeeze(1)

    net = torch.nn.Module  # placeholder to keep names tidy
    m = Net()
    opt = torch.optim.Adam(m.parameters(), 1e-3)
    t = time.time()
    for epoch in range(3):
        perm = torch.randperm(n)
        for i in range(0, n, 2048):
            idx = perm[i : i + 2048]
            loss = torch.nn.functional.mse_loss(m(route[idx], how[idx], num[idx]), y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
    out["embedding_mlp_500k_rows_3_epochs_seconds"] = round(time.time() - t, 1)
    out["final_mse"] = round(float(loss), 3)
    seq = torch.randn(200_000, 10, 6)
    ys = seq.sum((1, 2))
    lstm = torch.nn.LSTM(6, 64, batch_first=True)
    head = torch.nn.Linear(64, 1)
    opt = torch.optim.Adam(list(lstm.parameters()) + list(head.parameters()), 1e-3)
    t = time.time()
    for i in range(0, 200_000, 1024):
        o, _ = lstm(seq[i : i + 1024])
        loss = torch.nn.functional.mse_loss(head(o[:, -1]).squeeze(1), ys[i : i + 1024])
        opt.zero_grad()
        loss.backward()
        opt.step()
    out["lstm_200k_sequences_1_epoch_seconds"] = round(time.time() - t, 1)
except Exception as e:  # noqa: BLE001
    out["error"] = (type(e).__name__ + ": " + str(e))[:400]
print("RESULT_JSON " + json.dumps(out))
