#!/usr/bin/env python3
import os
import argparse
import concurrent.futures
import json
import re

from jira import JIRA
from functools import cached_property

from asana_exporter.utils import LOG, with_lock


class JiraImporter(object):

    def __init__(self, auth_token, auth_email, source, target_project,
                 asana_team, project_include_filter,
                 project_exclude_filter, import_label):
        self.source = source
        self.target_project = target_project
        self.asana_team = asana_team
        self.project_include_filter = project_include_filter
        self.project_exclude_filter = project_exclude_filter
        self.import_label = import_label
        self._jira = JIRA(server="https://warthogs.atlassian.net",
                          basic_auth=(auth_email,
                                      auth_token))

    @cached_property
    @with_lock
    def jira(self):
        return self._jira

    @cached_property
    @with_lock
    def project(self):
        return self._jira.project(self.target_project)

    @property
    def asana_team_id(self):
        teams_path = os.path.join(self.source, 'teams.json')
        with open(teams_path) as fd:
            for t in json.loads(fd.read()):
                if t['name'] == self.asana_team:
                    return t['gid']

    @cached_property
    @with_lock
    def asana_projects(self):
        projects_path = os.path.join(self.source, 'teams', self.asana_team_id,
                                     'projects.json')
        if not os.path.exists(projects_path):
            LOG.debug("asana team '{}' has no projects to import".
                      format(self.asana_team))
            return []

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
            if ast:
                dl_path = os.path.join(ppath, 'tasks', at['gid'], 'subtasks',
                                       ast['gid'], 'attachments',
                                       "{}_download".format(a['gid']))
            else:
                dl_path = os.path.join(ppath, 'tasks', at['gid'],
                                       'attachments',
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
            if not story['text']:
                LOG.debug("skipping comment with empty body (story gid={})".
                          format(story['gid']))
                continue

            LOG.info("adding comment to subtask: '{}...'".
                     format(story['text'][:20]))
            self.jira.add_comment(subtask, story['text'])

    def add_attachments_to_subtask(self, subtask, ppath, at, ast=None):
        attachments = self.asana_task_attachments(ppath, at, ast)
        task = ast or at
        ttype = ''
        if ast:
            ttype = "sub"

        if not attachments:
            LOG.debug("asana {}task '{}' has no attachments".
                      format(ttype, task['name']))
            return

        LOG.debug("asana {}task '{}' has '{}' attachments".
                  format(ttype, task['name'], len(attachments)))

        issue = self.jira.issue(subtask.key)
        existing = [str(a.filename) for a in issue.fields.attachment]
        for attachment in attachments:
            LOG.info("adding attachment '{}' to subtask".
                     format(attachment['name']))
            with open(attachment['local_path'], 'rb') as fd:
                if attachment['name'] in existing:
                    LOG.debug("attachment with filename '{}' already exists - "
                              "skipping".format(attachment['name']))
                    continue

                LOG.info("attaching name={} file={}".
                         format(attachment['name'], fd.name))
                self.jira.add_attachment(subtask, fd, attachment['name'])

    def sanitise_summary(self, summary):
        summary = summary.strip()
        summary = summary.replace('\n', ' ')
        # jira supports max 255 chars for summary
        return summary[:255]

    def import_asana_subtasks(self, jira_task, ppath, at, existing_subtasks,
                              desc):
        asana_subtasks = self.asana_project_task_subtasks(ppath, at)
        LOG.info("importing {} asana subtasks as subtasks".
                 format(len(asana_subtasks)))
        for ast in self.asana_project_task_subtasks(ppath, at):
            summary = self.sanitise_summary(">> {}".format(ast['name']))
            subtask = None
            for _st in existing_subtasks:
                if _st.fields.summary == summary:
                    subtask = _st
                    break

            create = True
            if subtask:
                LOG.debug("subtask for asana subtask '{}' already exists "
                          "- skipping create".format(ast['name']))
                current_status = str(subtask.fields.status)
                if current_status != 'Backlog':
                    LOG.debug("subtask has status '{}' - needs to be "
                              "'Backlog' to apply updates "
                              "- skipping update".format(current_status))
                    continue

                create = False

            labels = None
            if self.import_label:
                labels = [self.import_label]

            if create:
                LOG.info("creating subtask from asana task subtask '{}'".
                         format(ast['name']))

                subtask = self.jira.create_issue(
                                            project=self.project.key,
                                            description=desc,
                                            labels=labels,
                                            summary=summary,
                                            issuetype={'name': 'Sub-task'},
                                            parent={'key': jira_task.key})
            else:
                LOG.info("updating subtask from asana task subtask '{}'".
                         format(ast['name']))

                subtask.fields.description = desc
                subtask.fields.labels = labels
                subtask.fields.summary = summary

            try:
                if create:
                    self.add_comments_to_subtask(subtask, ppath, at, ast)

                self.add_attachments_to_subtask(subtask, ppath, at, ast)
                self.jira.transition_issue(subtask, 'DONE')
            except Exception as exc:
                LOG.error("failed to import task '{}' subtask '{}': {}".
                          format(at['gid'], ast['gid'], exc))
                if create:
                    subtask.delete()

    def _import_asana_project(self, pname, ppath):
        asana_tasks = self.asana_project_tasks(ppath)
        LOG.info("importing asana project '{}' with {} tasks".
                 format(pname, len(asana_tasks)))

        pname = pname.strip()
        task = None
        for _task in self.project_issues:
            if _task.fields.summary == pname:
                task = _task
                break

        desc = ("Imported from Asana team '{}' project '{}'".
                format(self.asana_team, pname))

        labels = None
        if self.import_label:
            labels = [self.import_label]

        if task is None:
            LOG.info("creating task '{}'".format(pname))
            task = self.jira.create_issue(project=self.project.key,
                                          labels=labels,
                                          summary=pname,
                                          description=desc,
                                          issuetype={'name': 'Task'})
        else:
            LOG.debug("task '{}' already exists - skipping create".
                      format(pname))
            current_status = str(task.fields.status)
            if current_status != 'Backlog':
                LOG.warning("tasks must be in state 'Backlog' if they "
                            "are to be modified - task '{}' is in state '{}' "
                            "- ignoring task".
                            format(task.fields.summary, current_status))
                return

        LOG.debug("importing {} tasks to '{}'".format(len(asana_tasks), pname))
        existing_subtasks = task.fields.subtasks
        for at in asana_tasks:
            if at['name'] == '':
                LOG.debug("skipping asana task gid={} with no name".
                          format(at['gid']))
                continue

            subtask = None
            summary = self.sanitise_summary(at['name'])
            for _st in existing_subtasks:
                if _st.fields.summary == summary:
                    subtask = _st
                    break

            create = True
            if subtask:
                current_status = str(subtask.fields.status)
                if current_status != 'Backlog':
                    LOG.debug("subtask has status '{}' - needs to be "
                              "'Backlog' to apply updates "
                              "- skipping update".format(current_status))
                    continue

                create = False

            if create:
                LOG.info("creating subtask '{}'". format(summary))
                subtask = self.jira.create_issue(
                                            project=self.project.key,
                                            description=desc,
                                            summary=summary,
                                            issuetype={'name': 'Sub-task'},
                                            parent={'key': task.key})
            else:
                LOG.info("updating subtask from asana task '{}'".
                         format(at['name']))

                subtask.fields.description = desc
                subtask.fields.labels = labels
                subtask.fields.summary = summary

            try:
                if create:
                    self.add_comments_to_subtask(subtask, ppath, at)

                self.add_attachments_to_subtask(subtask, ppath, at)
                self.jira.transition_issue(subtask, 'DONE')
            except Exception as exc:
                LOG.error("failed to import task '{}': {}".format(at['gid'],
                                                                  exc))
                if create:
                    subtask.delete()
            else:
                LOG.debug("subtask '{}' already exists - skipping create".
                          format(summary))

            self.import_asana_subtasks(task, ppath, at, existing_subtasks,
                                       desc)

        # Leave this till the end since tasks cant be deleted when in the DONE
        # state.
        self.jira.transition_issue(task, 'DONE')
        LOG.info("project '{}' import complete.".format(pname))

    def import_asana_project(self, pname, ppath):
        try:
            return self._import_asana_project(pname, ppath)
        except Exception:
            LOG.error("an exception occurred while importing project '{}' "
                      "from team '{}'".format(pname, self.asana_team))
            raise

    @cached_property
    def project_issues(self):
        start_at = 0
        limit = 50
        issues = []
        query = "project = {} AND issuetype = Task".format(self.target_project)
        while True:
            _issues = self.jira.search_issues(jql_str=query, startAt=start_at,
                                              maxResults=limit)
            if not _issues:
                return issues

            start_at += limit
            issues.extend(_issues)

    def import_data(self):
        if not os.path.exists(self.source):
            LOG.warning("path not found {}".format(self.source))
            return

        LOG.info("importing Asana team '{}' from {}".format(self.asana_team,
                                                            self.source))

        LOG.info("pre-loading issues for Jira project '{}'".
                 format(self.target_project))
        self.project_issues

        jobs = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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

                jobs[executor.submit(self.import_asana_project, pname,
                                     ppath)] = pname

            for job in concurrent.futures.as_completed(jobs):
                job.result()

        LOG.info("import complete.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', default=False,
                        help=("enable debug logging"))
    parser.add_argument('--token', type=str,
                        default=None, help="Jira api token")
    parser.add_argument('--email', type=str,
                        default=None, help="Jira api email")
    parser.add_argument('--asana-team', type=str,
                        default=None, help="Asana Team")
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
    parser.add_argument('--import-label', type=str,
                        default=None,
                        help=("Tag imported resources with this label."))

    args = parser.parse_args()
    if args.debug:
        LOG.set_level('debug')

    ji = JiraImporter(args.token, args.email, args.export_path,
                      args.jira_project,
                      args.asana_team,
                      project_include_filter=args.project_filter,
                      project_exclude_filter=args.exclude_projects,
                      import_label=args.import_label)
    ji.import_data()


if __name__ == "__main__":
    main()
