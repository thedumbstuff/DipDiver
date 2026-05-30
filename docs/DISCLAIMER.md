# Disclaimer

**Read this before reading any other document in this repository, and certainly before running any code.**

## Not financial advice

DipDiver is a software research project. Nothing in this repository — no code, document, scoreboard row, agent output, or commit message — constitutes:

- Investment advice
- Financial advice
- Tax advice
- Legal advice
- A recommendation to buy, sell, or hold any security, commodity, currency, derivative, or other financial instrument
- A solicitation of an offer to buy or sell anything
- An offer to manage assets on your behalf

If you are not qualified to evaluate a quantitative trading system on your own — including reading every line of code that touches an order — you should not use this software with real money. Consult a licensed financial professional.

## No warranty

This software is provided "as is" without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, and non-infringement. In no event shall the authors or contributors be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.

This is the standard MIT/Apache-style disclaimer and it means what it says: **if this software loses you money, that is your loss, not ours.**

## Trading risk

Trading securities, derivatives, and digital assets involves substantial risk of loss. Past performance — including any backtest, paper-trade record, scoreboard row, benchmark, or screenshot in this repository — is not indicative of future results. You can lose more than your initial investment in margin or derivatives accounts. Automated trading systems can fail in ways their authors did not anticipate, including but not limited to:

- Model failure (the strategy stops working)
- Data failure (bad ticks, missing data, vendor outage)
- Execution failure (broker outage, partial fills, slippage)
- Connectivity failure (network, exchange, cloud provider)
- Code failure (bugs, race conditions, configuration errors)
- LLM failure (hallucination, prompt injection, provider outage, cost spike)
- Regulatory action against you or your broker

Any one of these failures can wipe out an account. Several have, in well-documented public incidents involving systems built by people with more experience than this project's authors.

## Regulatory considerations

Algorithmic and automated trading is regulated differently in every jurisdiction. Examples (non-exhaustive, not legal advice):

- **United States:** SEC and FINRA rules apply to securities; CFTC to derivatives; algorithmic trading may trigger additional registration. Pattern day-trader rules apply at $25k.
- **European Union:** MiFID II algo-trading rules require systems and risk controls; some activities require authorisation.
- **United Kingdom:** FCA SYSC algorithmic trading requirements.
- **India:** SEBI algo-trading framework; brokers (Zerodha, Angel, etc.) require explicit approval for API/algo orders; recent rules around retail algo strategies are evolving.
- **Singapore:** MAS requirements for licensed entities.
- **Crypto:** patchwork by jurisdiction; many places treat it as a security or commodity for tax even if not for regulation.

**You are responsible for knowing what applies to you.** Running this software with a broker that has not authorised your specific algorithmic use can result in account closure, frozen funds, and regulatory action.

## LLM-specific risks

DipDiver uses Large Language Models for research and risk decisions. LLMs:

- Hallucinate plausible-sounding but wrong outputs
- Are sensitive to prompt phrasing in ways that can change behaviour silently
- Can be manipulated by injection attacks via news feeds, social data, or fundamental filings text
- Have non-deterministic costs that can spike unexpectedly
- Are subject to provider outages and rate limits at the worst possible moments
- May produce outputs that violate the law (e.g. market-manipulation patterns) if not constrained

The architecture (see [`ARCHITECTURE.md`](ARCHITECTURE.md)) places LLMs upstream of deterministic execution and gives them no direct path to the broker. This mitigates but does not eliminate these risks.

## No live record

At the time of writing, DipDiver has **never been used with real capital and has no live performance record.** Any claim otherwise — by anyone — is false. The validation methodology in [`VALIDATION.md`](VALIDATION.md) describes the gates that would have to be passed before real capital could be deployed; we do not claim to have passed them.

## Public scoreboard caveat

If and when the forward-eval scoreboard ([`VALIDATION.md`](VALIDATION.md)) is populated, it reflects paper-trading results on a specific universe with a specific configuration. **It is not your portfolio.** It does not include your taxes, your broker's commission schedule, your slippage in your account size, or your behavioural reaction to drawdown. A green scoreboard is necessary evidence; it is not sufficient evidence; it is not your forecast.

## Use of this software constitutes acceptance

By cloning, forking, running, or contributing to this repository, you acknowledge that you have read and understood this disclaimer and that you accept full responsibility for any consequence of your use of the software.

If you do not accept these terms, do not use the software.
