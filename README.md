# Overview

Extracts Asana projects along with their tasks and resources and saved in a tree structure of json files making it easy to import into Jira.

The Jira import part is still TODO.

## Usage

You will first need to generate a Personal Access Token. See https://developers.asana.com/docs/personal-access-token for instructions on how to do this.

Once you have this run the tool as follows. See https://developers.asana.com/docs/workspaces on workspaces, this is usually your organisation name. The tool must be run in the context of a team. You can run it multile times to get information from multiple teams and they will be aggregated under the export path.

```
./asana_to_jira/client.py --token MY_TOKEN --workspace WORKSPACE_ID --team MY_TEAM
```
