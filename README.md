# dwyt-runner

A **dwyt** futtatója: a felhasználó saját gépén vagy VPS-én fut (Linux, Windows, macOS), és kifelé kapcsolódik a központhoz ([dwyt-central](https://github.com/kzkz22/dwyt-central)). Letölt (yt-dlp), feliratot kér, átír (felhőben a saját kulccsal, vagy helyben a GPU-n), összefoglal és fordít (felhős vagy helyi LLM-mel). A kulcsok csak itt, helyben vannak.

- Terv és döntések: [dwyt-docs/docs/terv.md](https://github.com/kzkz22/dwyt-docs/blob/main/docs/terv.md) (8. fejezet)
- Központ–futtató protokoll: [dwyt-docs/docs/protokoll.md](https://github.com/kzkz22/dwyt-docs/blob/main/docs/protokoll.md)

## Állapot

Terv; kód még nincs. Telepítés tervezetten egy paranccsal, az `uv` csomagkezelővel; szerverre Docker-kép is.

## Licenc

MIT, lásd [LICENSE](LICENSE).
