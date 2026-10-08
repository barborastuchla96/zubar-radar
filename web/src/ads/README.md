# Ad network snippets (only used when ADS_PROVIDER=sklik)

These files are pasted in at **build** time, so rebuild (`./deploy/deploy.sh`) after editing.

- `cmp-head.html`: the consent-platform (CMP) code from Seznam's partner admin
  (partner.seznam.cz → CMP). It goes into `<head>` on every page. Sklik/Seznam SSP ads need
  TCF consent in the EU, and Seznam's CMP is free for partners.
- `<slot>.html`: the ad-zone code Sklik gives you for each placement. Slot names used by the site:
  `home-mid`, `city-zubar`, `city-praktik`, `city-pediatr`, `city-gynekolog`,
  `city-hygienistka`, `provider`. A slot without a file renders nothing.

Never paste code from anywhere except your own Sklik/Seznam admin. It runs on every visitor's page.
