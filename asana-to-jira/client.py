#!/usr/bin/env python3
import argparse
import os
import asana
import json

PROJECTS_JSON = 'projects.json'
EXPORT_DIR = 'asana_export'


class Asana(object):

    def __init__(self, token, workspace):
        self.workspace = workspace
        self.client = asana.Client.access_token(token)
        if not os.path.isdir(EXPORT_DIR):
            os.makedirs(EXPORT_DIR)

    @property
    def projects_json(self):
        return os.path.join(EXPORT_DIR, PROJECTS_JSON)

    @property
    def projects_dir(self):
        return os.path.join(EXPORT_DIR, 'projects')

    @property
    def tasks_dir(self):
        return os.path.join(EXPORT_DIR, 'tasks')

    @property
    def stories_dir(self):
        return os.path.join(EXPORT_DIR, 'stories')

    def get_projects(self):
        if os.path.exists(self.projects_json):
            print("INFO: using cached projects")
            with open(self.projects_json) as fd:
                projects = json.loads(fd.read())
        else:
            print("INFO: fetching projects")
            projects = []
            for p in self.client.projects.find_by_workspace(self.workspace):
                projects.append(p)

            with open(self.projects_json, 'w') as fd:
                fd.write(json.dumps(projects))

        return projects

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

        return stories

    def load(self):
        print("INFO: starting extraction")
        if not os.path.isdir(EXPORT_DIR):
            os.makedirs(EXPORT_DIR)

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
    args = parser.parse_args()
    Asana(token=args.token, workspace=args.workspace).load()
