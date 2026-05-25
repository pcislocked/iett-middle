git checkout -b release/v0.4.0
git add app/ tests/
git commit -m "feat: release v0.4.0 with Multi-Probe amenities, ContextVar cache and Opportunistic caching"
git push -u origin release/v0.4.0
gh pr create --title "Release v0.4.0: Multi-Probe and Opportunistic Caching" --body "Introduced X-IETT-Updated-At header propagation via ContextVars, multi-probe fleet amenities tracking, and opportunistic cache warming for stops."
