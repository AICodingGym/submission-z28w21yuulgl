from django.db import models
from django.db.models import signals
from django.db.models.deletion import Collector
from django.test import TestCase


class DeferOrigin(models.Model):
    pass


class DeferRelated(models.Model):
    origin = models.ForeignKey(DeferOrigin, models.CASCADE)
    payload = models.TextField(default='')


class DeferLeaf(models.Model):
    # Prevents DeferRelated from being fast-deleted so it has to be fetched.
    related = models.ForeignKey(DeferRelated, models.CASCADE)


class DeferToFieldRef(models.Model):
    origin = models.ForeignKey(DeferOrigin, models.CASCADE)
    code = models.CharField(max_length=10, unique=True)
    payload = models.TextField(default='')


class DeferToFieldLeaf(models.Model):
    ref = models.ForeignKey(DeferToFieldRef, models.CASCADE, to_field='code')


class DeferSetNull(models.Model):
    # Not fast-deletable (SET_NULL) and nothing references it.
    origin = models.ForeignKey(DeferOrigin, models.SET_NULL, null=True)
    payload = models.TextField(default='')


class SelectRelatedManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().select_related('origin')


class DeferSelectRelated(models.Model):
    origin = models.ForeignKey(DeferOrigin, models.CASCADE)
    payload = models.TextField(default='')

    objects = SelectRelatedManager()

    class Meta:
        base_manager_name = 'objects'


class DeferSelectRelatedLeaf(models.Model):
    related = models.ForeignKey(DeferSelectRelated, models.CASCADE)


def receiver(**kwargs):
    pass


class DeferredDeleteTests(TestCase):
    def collect(self, obj):
        collector = Collector(using='default')
        collector.collect([obj])
        return collector

    def test_only_referenced_fields_fetched_on_cascade(self):
        origin = DeferOrigin.objects.create()
        related = DeferRelated.objects.create(origin=origin, payload='junk')
        DeferLeaf.objects.create(related=related)
        collector = self.collect(origin)
        instances = collector.data[DeferRelated]
        self.assertEqual(len(instances), 1)
        for obj in instances:
            self.assertEqual(obj.get_deferred_fields(), {'origin_id', 'payload'})
        origin.delete()
        self.assertFalse(DeferRelated.objects.exists())
        self.assertFalse(DeferLeaf.objects.exists())

    def test_to_field_reference_fetched_and_cascades(self):
        origin = DeferOrigin.objects.create()
        ref = DeferToFieldRef.objects.create(origin=origin, code='abc', payload='junk')
        DeferToFieldLeaf.objects.create(ref=ref)
        collector = self.collect(origin)
        for obj in collector.data[DeferToFieldRef]:
            self.assertEqual(obj.get_deferred_fields(), {'origin_id', 'payload'})
        origin.delete()
        self.assertFalse(DeferToFieldRef.objects.exists())
        self.assertFalse(DeferToFieldLeaf.objects.exists())

    def test_only_pk_fetched_when_nothing_references_related_model(self):
        origin = DeferOrigin.objects.create()
        set_null = DeferSetNull.objects.create(origin=origin, payload='junk')
        collector = self.collect(origin)
        updates = collector.field_updates[DeferSetNull]
        self.assertEqual(len(updates), 1)
        for instances in updates.values():
            for obj in instances:
                self.assertEqual(obj.get_deferred_fields(), {'origin_id', 'payload'})
        origin.delete()
        set_null.refresh_from_db()
        self.assertIsNone(set_null.origin)

    def assertNoDeferralWithSignal(self, signal):
        signal.connect(receiver, sender=DeferRelated)
        self.addCleanup(signal.disconnect, receiver, sender=DeferRelated)
        origin = DeferOrigin.objects.create()
        related = DeferRelated.objects.create(origin=origin, payload='junk')
        DeferLeaf.objects.create(related=related)
        collector = self.collect(origin)
        for obj in collector.data[DeferRelated]:
            self.assertEqual(obj.get_deferred_fields(), set())

    def test_no_deferral_with_pre_delete_receiver(self):
        self.assertNoDeferralWithSignal(signals.pre_delete)

    def test_no_deferral_with_post_delete_receiver(self):
        self.assertNoDeferralWithSignal(signals.post_delete)

    def test_select_related_base_manager_not_deferred(self):
        origin = DeferOrigin.objects.create()
        related = DeferSelectRelated.objects.create(origin=origin, payload='junk')
        DeferSelectRelatedLeaf.objects.create(related=related)
        origin.delete()
        self.assertFalse(DeferSelectRelated.objects.exists())
        self.assertFalse(DeferSelectRelatedLeaf.objects.exists())
