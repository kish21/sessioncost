# Security

SessionCost runs on your computer and reads your agent's session logs. It calls no AI model, and it sends nothing
anywhere, with one exception: when the agent installs a package, the guard sends that package's name to its registry
(pypi.org or registry.npmjs.org) to check how new that version is. `sessioncost guard fresh-releases off` stops it.

## Reporting a problem

Please report security problems privately, not in a public issue: on GitHub, open the **Security** tab of this
repository and choose **Report a vulnerability**. You will get an answer within a few days.

## Supported versions

Only the latest release is supported; fixes ship as a new version on PyPI and GitHub.
