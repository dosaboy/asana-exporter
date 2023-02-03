#!/usr/bin/env python3
import os
import argparse
import concurrent.futures
import json
import re

from jira import JIRA
from functools import cached_property

from asana_exporter.utils import LOG


class JiraImporter(object):

    def __init__(self, auth_token, auth_email, source, target_project,
                 asana_team, asana_project, project_include_filter,
                 project_exclude_filter,):
        self.source = source
        self.target_project = target_project
        self.asana_team = asana_team
        self.asana_project = asana_project
        self.project_include_filter = project_include_filter
        self.project_exclude_filter = project_exclude_filter
        self.jira = JIRA(server="https://warthogs.atlassian.net",
                         basic_auth=(auth_email,
                                     auth_token))
        self.project = self.jira.project(target_project)

    @cached_property
    def components(self):
        return [c.name for c in self.project.components]

    @cached_property
    def asana_team_id(self):
        teams_path = os.path.join(self.source, 'teams.json')
        with open(teams_path) as fd:
            for t in json.loads(fd.read()):
                if t['name'] == self.asana_team:
                    return t['gid']

    @cached_property
    def asana_projects(self):
        projects_path = os.path.join(self.source, 'teams', self.asana_team_id,
                                     'projects.json')
        template = os.path.join(self.source, 'teams', self.asana_team_id)
        template += "/projects/{}"
        with open(projects_path) as fd:
            return [(p['name'], template.format(p['gid']))
                    for p in json.loads(fd.read())]

    def asana_task_stories(self, ppath, at, ast):
        if ast:
            spath = os.path.join(ppath, 'tasks', at['gid'], 'subtasks',
                                 ast['gid'], 'stories.json')
        else:
            spath = os.path.join(ppath, 'tasks', at['gid'], 'stories.json')

        if not os.path.exists(spath):
            LOG.debug("asana task '{}' has no stories to import".
                      format(at['gid']))
            return []

        with open(spath) as fd:
            return json.loads(fd.read())

    def asana_task_attachments(self, ppath, at, ast):
        if ast:
            apath = os.path.join(ppath, 'tasks', at['gid'], 'subtasks',
                                 ast['gid'], 'attachments.json')
        else:
            apath = os.path.join(ppath, 'tasks', at['gid'], 'attachments.json')

        if not os.path.exists(apath):
            LOG.debug("asana task '{}' has no attachments to import".
                      format(at['gid']))
            return []

        with open(apath) as fd:
            attachments = json.loads(fd.read())

        for a in attachments:
            dl_path = os.path.join(ppath, 'tasks', at['gid'], 'attachments',
                                   "{}_download".format(a['gid']))
            a['local_path'] = dl_path

        return attachments

    def asana_project_tasks(self, p):
        with open(os.path.join(p, 'tasks.json')) as fd:
            return json.loads(fd.read())

    def asana_project_task_subtasks(self, p, t):
        with open(os.path.join(p, 'tasks', t['gid'], 'subtasks.json')) as fd:
            return json.loads(fd.read())

    def add_comments_to_subtask(self, subtask, ppath, at, ast=None):
        for story in self.asana_task_stories(ppath, at, ast):
            LOG.debug("adding comment to subtask: '{}...'".
                      format(story['text'][:20]))
            self.jira.add_comment(subtask, story['text'])

    def add_attachments_to_subtask(self, subtask, ppath, at, ast=None):
        attachments = self.asana_task_attachments(ppath, at, ast)
        task = ast or at
        if not attachments:
            LOG.debug("asana task '{}' has no attachments".
                      format(task['name']))
        else:
            LOG.debug("asana task '{}' has '{}' attachments".
                      format(task['name'], len(attachments)))

        for attachment in attachments:
            LOG.debug("adding attachments to subtask: {}".
                      format(attachment['name'][:10]))
            with open(attachment['local_path'], 'rb') as fd:
                LOG.debug("attaching name={} file={}".
                          format(attachment['name'], fd.name))
                self.jira.add_attachment(subtask, fd, attachment['name'])

    def create_jira_task(self, pname, ppath):
        asana_tasks = self.asana_project_tasks(ppath)
        LOG.debug("creating jira task '{}' with {} subtasks".
                  format(pname, len(asana_tasks)))

        task = None
        for _task in self.project_issues:
            if _task.fields.summary == pname:
                LOG.debug("task '{}' already exists - skipping create".
                          format(pname))
                task = _task
                break

        desc = ("Imported from Asana team '{}' project '{}'".
                format(self.asana_team, pname))

        if task is None:
            LOG.debug("creating task '{}'".format(pname))
            task = self.jira.create_issue(project=self.project.key,
                                          summary=pname,
                                          description=desc,
                                          issuetype={'name': 'Task'})
            self.jira.transition_issue(task, 'DONE')

        LOG.debug("adding {} subtasks to '{}'".format(len(asana_tasks), pname))
        existing_subtasks = task.fields.subtasks
        for at in asana_tasks:
            if at['name'] == '':
                LOG.debug("skipping asana task gid={} with no name".
                          format(at['gid']))
                continue

            subtask = None
            for _st in existing_subtasks:
                if _st.fields.summary == at['name']:
                    subtask = _st
                    break

            if not subtask:
                LOG.debug("creating subtask '{}'". format(at['name']))
                subtask = self.jira.create_issue(
                                            project=self.project.key,
                                            description=desc,
                                            summary=at['name'],
                                            issuetype={'name': 'Sub-task'},
                                            parent={'key': task.key})
                self.jira.transition_issue(subtask, 'DONE')
                self.add_comments_to_subtask(subtask, ppath, at)
                self.add_attachments_to_subtask(subtask, ppath, at)
            else:
                LOG.debug("subtask '{}' already exists - skipping create".
                          format(at['name']))

            asana_subtasks = self.asana_project_task_subtasks(ppath, at)
            LOG.debug("adding {} asana subtasks as subtasks".
                      format(len(asana_subtasks)))
            for ast in self.asana_project_task_subtasks(ppath, at):
                _st_name = ">> {}".format(ast['name'])
                subtask = None
                for _st in existing_subtasks:
                    if _st.fields.summary == _st_name:
                        subtask = _st
                        break

                if subtask:
                    LOG.debug("subtask for asana subtask '{}' already exists "
                              "- skipping create".format(ast['name']))
                else:
                    LOG.debug("creating subtask from asana task subtask '{}'".
                              format(ast['name']))
                    subtask = self.jira.create_issue(
                                                project=self.project.key,
                                                description=desc,
                                                summary=_st_name,
                                                issuetype={'name': 'Sub-task'},
                                                parent={'key': task.key})
                    self.jira.transition_issue(subtask, 'DONE')
                    self.add_comments_to_subtask(subtask, ppath, at, ast)
                    self.add_attachments_to_subtask(subtask, ppath, at, ast)

    @cached_property
    def project_issues(self):
        return self.jira.search_issues(jql_str="project = {}".
                                       format(self.target_project))

    def import_data(self):
        if not os.path.exists(self.source):
            LOG.warning("path not found {}".format(self.source))
            return

        LOG.debug("importing data from {}".format(self.source))
        jobs = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for pname, ppath in self.asana_projects:
                if self.project_include_filter:
                    if not re.search(self.project_include_filter, pname):
                        LOG.info("skipping asana project {}".format(pname))
                        continue

                if self.project_exclude_filter:
                    if re.search(self.project_exclude_filter, pname):
                        LOG.info("skipping asana project {}".
                                 format(pname))
                        continue

                jobs[executor.submit(self.create_jira_task, pname,
                                     ppath)] = pname

            for job in concurrent.futures.as_completed(jobs):
                job.result()
                LOG.debug("project '{}' import complete.".format(jobs[job]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', default=True,
                        help=("enable debug logging"))
    parser.add_argument('--token', type=str,
                        default=None, help="Jira api token")
    parser.add_argument('--email', type=str,
                        default=None, help="Jira api email")
    parser.add_argument('--asana-team', type=str,
                        default=None, help="Asana Team")
    parser.add_argument('--asana-project', type=str,
                        default=None, help="Asana Project")
    parser.add_argument('--jira-project', type=str,
                        default=None, help="Jira project")
    parser.add_argument('--export-path', type=str,
                        default=None, required=True,
                        help="path to data we want to import.")
    parser.add_argument('--exclude-projects', type=str,
                        default=None,
                        help=("Regular expression filter used to exclude "
                              "projects."))
    parser.add_argument('--project-filter', type=str,
                        default=None,
                        help=("Regular expression filter used to include "
                              "projects."))

    args = parser.parse_args()
    if args.debug:
        LOG.set_level('debug')

    ji = JiraImporter(args.token, args.email, args.export_path,
                      args.jira_project,
                      args.asana_team, args.asana_project,
                      project_include_filter=args.project_filter,
                      project_exclude_filter=args.exclude_projects)
    ji.import_data()


if __name__ == "__main__":
    main()
