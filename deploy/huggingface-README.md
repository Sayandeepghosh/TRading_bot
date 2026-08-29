---
title: Equity Analyser
emoji: "\U0001F4C8"
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Equity Analyser

NSE equity screener that shows entry triggers, stop losses and position sizes
with the reasoning attached. Advisory only: it never places an order.

## To use this on Hugging Face Spaces

1. Create a Space, choose **Docker** as the SDK, blank template.
2. Copy this file to the Space repo as `README.md` (the front matter above is
   what configures the Space, so it has to be the README).
3. Copy `Dockerfile`, `requirements.txt`, `run.py`, `config/` and `src/` across.
4. Add a Space secret or variable `PORT` with value `7860`, which is the port
   Spaces routes to. `app_port` above also declares it.

## Read this before you publish

A free Space is **public**. Anyone can open it, and this dashboard has no
authentication: the settings page and the position routes accept writes. On a
public Space, treat it as read-only and do not record real positions, or your
capital figure and holdings become public and editable.

For anything private, run it locally or on your own VM behind a password. See
`deploy/README.md` in the project.
