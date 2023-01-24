#!/usr/bin/env python3
import argparse
import os
import asana
import json
from functools import cached_property

EXPORT_DIR = 'asana_export'


class AsanaExtractor(object):

    def __init__(self, token, workspace, teamname):
        self.workspace = workspace
        self.teamname = teamname
        self.client = asana.Client.access_token(token)
        if not os.path.isdir(EXPORT_DIR):
            os.makedirs(EXPORT_DIR)

    @cached_property
    def team(self):
        for t in self.get_teams():
            if t['name'] == self.teamname:
                return t

        raise Exception("No team found with name '{}'".format(self.teamname))

    @property
    def teams_json(self):
        return os.path.join(EXPORT_DIR, 'teams.json')

    @property
    def projects_json(self):
        return os.path.join(EXPORT_DIR, 'projects.json')

    @property
    def projects_dir(self):
        return os.path.join(EXPORT_DIR, 'projects')

    @property
    def tasks_dir(self):
        return os.path.join(EXPORT_DIR, 'tasks')

    @property
    def stories_dir(self):
        return os.path.join(EXPORT_DIR, 'stories')

    def get_teams(self):
        if os.path.exists(self.teams_json):
            print("INFO: using cached teams")
            with open(self.teams_json) as fd:
                teams = json.loads(fd.read())
        else:
            print("INFO: fetching teams")
            teams = []
            for p in self.client.teams.find_by_organization(self.workspace):
                teams.append(p)

            with open(self.teams_json, 'w') as fd:
                fd.write(json.dumps(teams))

        print("INFO: loaded {} teams".format(len(teams)))
        return teams

    def get_projects(self):
        if os.path.exists(self.projects_json):
            print("INFO: using cached projects")
            with open(self.projects_json) as fd:
                projects = json.loads(fd.read())
        else:
            print("INFO: fetching projects")
            projects = []
            for p in self.client.projects.find_by_workspace(
                                                      workspace=self.workspace,
                                                      team=self.team['gid']):
                projects.append(p)

            with open(self.projects_json, 'w') as fd:
                fd.write(json.dumps(projects))

        print("INFO: loaded {} projects".format(len(projects)))
        return projects

    def get_project_templates(self):
        root_path = path = os.path.join(self.projects_dir)
        if os.path.exists(os.path.join(root_path, 'templates.json')):
            print("INFO: using cached project templates")
            with open(os.path.join(root_path, 'templates.json')) as fd:
                templates = json.loads(fd.read())
        else:
            print("INFO: fetching project templates for team '{}'".
                  format(self.team['name']))
            path = os.path.join(root_path, 'templates')
            os.makedirs(path)

            templates = []
            for t in self.client.projects.find_by_team(team=self.team['gid'],
                                                       is_template=True):
                templates.append(t)

            with open(os.path.join(root_path, 'templates.json'), 'w') as fd:
                fd.write(json.dumps(templates))

        print("INFO: loaded {} templates".format(len(templates)))
        return templates

    def get_project_tasks(self, project):
        root_path = path = os.path.join(self.projects_dir, project['gid'])
        if os.path.exists(os.path.join(root_path, 'tasks.json')):
            print("INFO: using cached tasks for project '{}'".
                  format(project['name']))
            with open(os.path.join(root_path, 'tasks.json')) as fd:
                tasks = json.loads(fd.read())
        else:
            print("INFO: fetching tasks for project '{}'".
                  format(project['name']))
            path = os.path.join(root_path, 'tasks')
            os.makedirs(path)

            tasks = []
            for t in self.client.projects.tasks(project['gid']):
                tasks.append(t)

            for t in tasks:
                if os.path.exists(os.path.join(path, t['gid'])):
                    continue

                os.makedirs(os.path.join(path, t['gid']))

            with open(os.path.join(root_path, 'tasks.json'), 'w') as fd:
                fd.write(json.dumps(tasks))

        print("INFO: loaded {} tasks".format(len(tasks)))
        return tasks

    def get_task_stories(self, project, task):
        root_path = os.path.join(self.projects_dir, project['gid'], 'tasks',
                                 task['gid'])
        if os.path.exists(os.path.join(root_path, 'stories.json')):
            print("INFO: using cached stories for project '{}' task '{}'".
                  format(project['name'], task['name']))
            with open(os.path.join(root_path, 'stories.json')) as fd:
                stories = json.loads(fd.read())
        else:
            print("INFO: fetching stories for task '{}'".format(task['name']))
            path = os.path.join(root_path, 'stories')
            os.makedirs(path)

            stories = []
            for s in self.client.stories.find_by_task(task['gid']):
                stories.append(s)

            for s in stories:
                with open(os.path.join(path, s['gid']), 'w') as fd:
                    fd.write(json.dumps(s))

            with open(os.path.join(root_path, 'stories.json'),
                      'w') as fd:
                fd.write(json.dumps(stories))

        print("INFO: loaded {} stories".format(len(stories)))
        return stories

    def load(self):
        print("INFO: starting extraction")
        if not os.path.isdir(EXPORT_DIR):
            os.makedirs(EXPORT_DIR)

        self.get_project_templates()
        for p in self.get_projects():
            if 'handover' in p['name'].lower():
                for t in self.get_project_tasks(p):
                    self.get_task_stories(p, t)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', type=str,
                        default=None, required=True, help="Asana api token")
    parser.add_argument('--workspace', type=str,
                        default=None, required=True, help="Asana workspace ID")
    parser.add_argument('--team', type=str,
                        default=None, required=True, help="Asana team name")
    args = parser.parse_args()
    AsanaExtractor(token=args.token, workspace=args.workspace,
                   teamname=args.team).load()
