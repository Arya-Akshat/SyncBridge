import { useState } from 'react';
import {
	Box,
	Button,
	Card,
	CardContent,
	Typography,
	Grid,
	Snackbar,
	Alert,
	CircularProgress,
} from '@mui/material';
import axios from 'axios';

const endpointMapping = {
	'Notion': 'notion',
	'Airtable': 'airtable',
	'HubSpot': 'hubspot',
};

export const DataForm = ({ integrationParams, selectedIntegrationType }) => {
	const [loadedData, setLoadedData] = useState(null);
	const [loading, setLoading] = useState(false);
	const [lastSynced, setLastSynced] = useState(null);
	const [error, setError] = useState(null);

	const integrationType = selectedIntegrationType || integrationParams?.type || 'HubSpot';
	const endpoint = endpointMapping[integrationType] || 'hubspot';

	const handleLoad = async () => {
		setLoading(true);
		setError(null);
		try {
			const formData = new FormData();
			formData.append('credentials', JSON.stringify(integrationParams?.credentials));
			const response = await axios.post(`http://localhost:8000/integrations/${endpoint}/load`, formData);
			setLoadedData(response.data);
			setLastSynced(new Date().toLocaleTimeString());
		} catch (e) {
			setError(e?.response?.data?.detail || e.message || 'Error loading data');
		} finally {
			setLoading(false);
		}
	};

	return (
		<Box display='flex' justifyContent='center' alignItems='center' flexDirection='column' width='100%' sx={{ mt: 3 }}>
			<Box display='flex' gap={2} mb={2} alignItems='center'>
				<Button
					onClick={handleLoad}
					disabled={loading}
					variant='contained'
				>
					{loading ? <CircularProgress size={20} /> : (loadedData ? 'Refresh Data' : 'Load Data')}
				</Button>
				<Button
					onClick={() => {
						setLoadedData(null);
						setLastSynced(null);
					}}
					variant='outlined'
					color='error'
					disabled={!loadedData}
				>
					Clear
				</Button>
				{lastSynced && (
					<Typography variant='body2' color='text.secondary'>
						Last synced: {lastSynced}
					</Typography>
				)}
			</Box>

			{loadedData && Array.isArray(loadedData) && (
				<Grid container spacing={2} sx={{ maxWidth: 800 }}>
					{loadedData.map((item, idx) => (
						<Grid item xs={12} sm={6} md={4} key={item.id || idx}>
							<Card variant='outlined' sx={{ height: '100%' }}>
								<CardContent>
									<Typography variant='caption' color='primary' gutterBottom>
										{(item.source ? item.source.toUpperCase() : integrationType.toUpperCase()) + ' - ' + (item.type || 'Unknown')}
									</Typography>
									<Typography variant='h6' component='div'>
										{item.name || 'Untitled'}
									</Typography>
									{item.created_at && (
										<Typography variant='body2' color='text.secondary'>
											Created: {new Date(item.created_at).toLocaleDateString()}
										</Typography>
									)}
									{item.updated_at && (
										<Typography variant='body2' color='text.secondary'>
											Updated: {new Date(item.updated_at).toLocaleDateString()}
										</Typography>
									)}
									{item.last_modified_time && (
										<Typography variant='body2' color='text.secondary'>
											Updated: {new Date(item.last_modified_time).toLocaleDateString()}
										</Typography>
									)}
								</CardContent>
							</Card>
						</Grid>
					))}
				</Grid>
			)}

			<Snackbar open={!!error} autoHideDuration={6000} onClose={() => setError(null)}>
				<Alert onClose={() => setError(null)} severity='error' sx={{ width: '100%' }}>
					{error}
				</Alert>
			</Snackbar>
		</Box>
	);
};
