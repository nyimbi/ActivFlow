"""Generic workflow engine views"""

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.urlresolvers import reverse, reverse_lazy
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views import generic

from activflow.core.constants import WORKFLOW_APPS, REQUEST_IDENTIFIER
from activflow.core.helpers import (
    get_errors,
    get_model,
    get_model_instance,
    get_form_instance,
    get_formset_instances,
    get_request_params,
    flow_config
)

from activflow.core.mixins import AccessDeniedMixin
from activflow.core.models import get_workflows_requests, get_task


@login_required
def workflows(request):
    """Lists down registered workflows"""
    return render(request, 'index.html', {'workflows': WORKFLOW_APPS})


class WorkflowDetail(LoginRequiredMixin, generic.TemplateView):
    """Generic view to list worflow requests & tasks"""
    template_name = 'core/workflow.html'

    def get_context_data(self, **kwargs):
        """Retrieve context data<"""
        context = super(WorkflowDetail, self).get_context_data(**kwargs)
        app_title = get_request_params('app_name', **kwargs)
        config = flow_config(app_title)
        model = config.FLOW[config.INITIAL]['model']().title
        context['requests'] = get_workflows_requests(app_title)
        context['request_identifier'] = REQUEST_IDENTIFIER
        context['workflow_title'] = config.TITLE
        context['description'] = config.DESCRIPTION
        context['initial'] = model

        return context


class ViewActivity(AccessDeniedMixin, generic.DetailView):
    """Generic view to display activity details"""
    template_name = 'core/detail.html'

    def dispatch(self, request, *args, **kwargs):
        """Overriding dispatch on DetailView"""
        self.model = get_model(**kwargs)
        denied = self.check(request, **kwargs)
        return denied if denied else super(ViewActivity, self).dispatch(
            request, *args, **kwargs)


class RollBackActivity(LoginRequiredMixin, generic.View):
    """Rollbacks workflow task"""
    @transaction.atomic
    def post(self, request, **kwargs):
        """POST request handler for rollback"""
        app_title = get_request_params('app_name', **kwargs)
        identifier = get_request_params('pk', **kwargs)
        get_task(identifier).rollback()

        return HttpResponseRedirect(
            reverse('workflow-detail', args=[app_title]))


class DeleteActivity(LoginRequiredMixin, generic.DeleteView):
    """Deletes activity instance"""
    def dispatch(self, request, *args, **kwargs):
        """Overriding dispatch on DeleteView"""
        self.model = get_model(**kwargs)
        self.success_url = reverse_lazy(
            'workflow-detail', args=[get_request_params(
                'app_name', **kwargs)])

        return super(DeleteActivity, self).dispatch(
            request, *args, **kwargs)


class CreateActivity(AccessDeniedMixin, generic.View):
    """Generic view to initiate activity"""
    def get(self, request, **kwargs):
        """GET request handler for Create operation"""
        form = get_form_instance(**kwargs)
        formsets = [formset(prefix=formset.form.__name__) for formset in get_formset_instances(extra=1, **kwargs)]
        context = {'form': form, 'formsets': formsets}

        denied = self.check(request, **kwargs)
        return denied if denied else render(
            request, 'core/create.html', context)

    @transaction.atomic
    def post(self, request, **kwargs):
        """POST request handler for Create operation"""
        model = get_model(**kwargs)
        form = get_form_instance(**kwargs)(request.POST)
        app_title = get_request_params('app_name', **kwargs)

        if form.is_valid():
            instance = model(**form.cleaned_data)
            instance = form.save()
            instruction = next(iter(filter(lambda key: 'add-' in key, request.POST)), None)

            if instruction:
                req = request.POST.copy()
                formsets = get_formset_instances(extra=1, **kwargs)

                for formset in formsets:
                    form_title = formset.form.__name__
                    if 'add-' + form_title.replace('Form', '') in instruction:
                        total_forms = form_title + '-TOTAL_FORMS'
                        req[total_forms] = int(req[total_forms]) + 1  
   
                formsets = [formset(
                    req, prefix=formset.form.__name__) for formset in formsets]

                context = {
                    'form': form,
                    'formsets': formsets,
                }

                return render(request, 'core/create.html', context)

            for formset in get_formset_instances(**kwargs):
                formset = formset(request.POST, instance=instance, prefix=formset.form.__name__)
                formset.save() if formset.is_valid() else print(formset.errors)

            if instance.is_initial:
                instance.initiate_request(request.user, app_title)
            else:
                instance.assign_task(
                    get_request_params('pk', **kwargs))
                instance.task.initiate()

            return HttpResponseRedirect(
                reverse('update', args=(
                    app_title, instance.title, instance.id)))
        else:
            context = {
                'form': form,
                'error_message': get_errors(form.errors)
            }

            return render(request, 'core/create.html', context)


class UpdateActivity(AccessDeniedMixin, generic.View):
    """Generic view to update activity"""
    def get(self, request, **kwargs):
        """GET request handler for Update operation"""
        instance = get_model_instance(**kwargs)
        form = get_form_instance(**kwargs)
        formsets = get_formset_instances(extra=1, **kwargs)
        context = {
            'form': form(instance=instance),
            'formsets': [formset(instance=instance, prefix=formset.form.__name__) for formset in formsets],
            'object': instance,
            'next': instance.next_activity()
        }

        denied = self.check(request, **kwargs)
        return denied if denied else render(
            request, 'core/update.html', context)

    @transaction.atomic
    def post(self, request, **kwargs):
        """POST request handler for Update operation"""
        redirect_to_update = False
        instance = get_model_instance(**kwargs)
        app_title = get_request_params('app_name', **kwargs)
        form = get_form_instance(
            **kwargs)(request.POST, instance=instance)

        if form.is_valid():
            form.save()

            for formset in get_formset_instances(**kwargs):
                formset = formset(request.POST, instance=instance, prefix=formset.form.__name__)
                formset.save() if formset.is_valid() else print(formset.errors)

            if 'save' in request.POST:
                redirect_to_update = True
                instance.update()
            elif 'finish' in request.POST:
                instance.finish()
            else:
                next_activity = request.POST['submit']
                if not instance.validate_rule(next_activity):
                    redirect_to_update = True
                else:
                    instance.task.submit(
                        app_title, self.request.user, next_activity)

            return HttpResponseRedirect(
                reverse('update', args=(
                    app_title, instance.title, instance.id))
            ) if redirect_to_update else HttpResponseRedirect(
                    reverse('workflow-detail', args=[app_title]))
        else:
            context = {
                'form': form,
                'object': instance,
                'next': instance.next_activity(),
                'error_message': get_errors(form.errors)
            }

            return render(request, 'core/update.html', context)
