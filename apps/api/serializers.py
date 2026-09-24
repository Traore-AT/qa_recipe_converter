from rest_framework import serializers
from apps.core.models import ConversionJob, ExtractedUseCase
from apps.qamanagement.models import UseCaseComment


class UseCaseCommentSerializer(serializers.ModelSerializer):
    author_full_name = serializers.SerializerMethodField()

    class Meta:
        model = UseCaseComment
        fields = ['id', 'author', 'author_full_name', 'content', 'created_at', 'updated_at']

    def get_author_full_name(self, obj):
        if obj.author.first_name or obj.author.last_name:
            return f'{obj.author.first_name} {obj.author.last_name}'.strip()
        return obj.author.username


class ExtractedUseCaseSerializer(serializers.ModelSerializer):
    comments = serializers.SerializerMethodField()

    class Meta:
        model = ExtractedUseCase
        fields = [
            'id', 'order', 'use_case_text', 'description', 'preconditions',
            'steps', 'expected_results', 'observed_results', 'is_automated', 'status',
            'jira_ticket', 'comments'
        ]

    def get_comments(self, obj):
        comments = obj.comments.all().order_by('created_at')
        return UseCaseCommentSerializer(comments, many=True).data


class ConversionJobSerializer(serializers.ModelSerializer):
    use_cases = ExtractedUseCaseSerializer(many=True, read_only=True)
    effective_excel_filename = serializers.CharField(read_only=True)

    class Meta:
        model = ConversionJob
        fields = [
            'id', 'created_at', 'status', 'source_filename',
            'use_cases_count', 'error_message', 'use_cases',
            'company_name', 'excel_filename', 'company_logo', 'effective_excel_filename'
        ]


class ConversionJobListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversionJob
        fields = ['id', 'created_at', 'status', 'source_filename', 'use_cases_count']


class UploadSerializer(serializers.Serializer):
    word_file = serializers.FileField()
    excel_template = serializers.FileField(required=False)
