/**
 * Token Debug Helper
 * Provides debugging tools for token authentication issues
 */

class TokenDebugger {
    constructor() {
        this.correctToken = 'tolery47382916';
        this.baseUrl = `${window.location.protocol}//${window.location.host}`;
    }

    // Test token validation manually
    async testTokenValidation(token = null) {
        const testToken = token || this.correctToken;
        console.log('🧪 Testing token validation...');
        console.log(`Token: ${testToken}`);
        
        try {
            // Test the new validation endpoint
            const response = await fetch(`${this.baseUrl}/api-production/validate-token`, {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${testToken}`,
                    'Content-Type': 'application/json'
                }
            });

            console.log(`Response status: ${response.status}`);
            console.log(`Response headers:`, response.headers);
            
            if (response.ok) {
                const data = await response.json();
                console.log('✅ Token validation successful:', data);
                return { success: true, data };
            } else {
                const errorData = await response.text();
                console.log('❌ Token validation failed:', errorData);
                return { success: false, error: errorData, status: response.status };
            }
        } catch (error) {
            console.error('🌐 Network error during token validation:', error);
            return { success: false, error: error.message, networkError: true };
        }
    }

    // Check current token status
    checkCurrentToken() {
        const storedToken = localStorage.getItem('api-token');
        const windowToken = window.apiToken;
        
        console.log('🔍 Current token status:');
        console.log(`localStorage token: ${storedToken}`);
        console.log(`window.apiToken: ${windowToken}`);
        console.log(`Correct token: ${this.correctToken}`);
        console.log(`Tokens match localStorage: ${storedToken === this.correctToken}`);
        console.log(`Tokens match window: ${windowToken === this.correctToken}`);
        
        return {
            stored: storedToken,
            window: windowToken,
            correct: this.correctToken,
            storedMatch: storedToken === this.correctToken,
            windowMatch: windowToken === this.correctToken
        };
    }

    // Test different endpoints
    async testEndpoints() {
        const token = this.correctToken;
        const endpoints = [
            '/api-production/validate-token',
            '/api-production/sessions',
            '/api/health'
        ];

        console.log('🎯 Testing multiple endpoints...');
        
        for (const endpoint of endpoints) {
            try {
                const url = `${this.baseUrl}${endpoint}`;
                console.log(`\n📡 Testing: ${url}`);
                
                const response = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'Content-Type': 'application/json'
                    }
                });

                console.log(`Status: ${response.status} ${response.statusText}`);
                
                if (response.ok) {
                    console.log('✅ Success');
                } else {
                    console.log('❌ Failed');
                }
            } catch (error) {
                console.log(`🌐 Network error: ${error.message}`);
            }
        }
    }

    // Fix token issues
    fixToken() {
        console.log('🔧 Fixing token issues...');
        
        // Set correct token
        localStorage.setItem('api-token', this.correctToken);
        window.apiToken = this.correctToken;
        
        // Update UI if elements exist
        const tokenInput = document.getElementById('api-token-input');
        const statusDiv = document.getElementById('token-status');
        
        if (tokenInput) {
            tokenInput.value = this.correctToken;
        }
        
        if (statusDiv) {
            statusDiv.textContent = 'Token fixed - ready to test';
            statusDiv.className = 'token-status info';
        }
        
        console.log('✅ Token has been set to correct value');
        return this.checkCurrentToken();
    }

    // Clear all token data
    clearToken() {
        console.log('🧹 Clearing all token data...');
        
        localStorage.removeItem('api-token');
        window.apiToken = null;
        
        const tokenInput = document.getElementById('api-token-input');
        const statusDiv = document.getElementById('token-status');
        
        if (tokenInput) {
            tokenInput.value = '';
        }
        
        if (statusDiv) {
            statusDiv.textContent = 'Token cleared';
            statusDiv.className = 'token-status info';
        }
        
        console.log('✅ All token data cleared');
    }

    // Run comprehensive debug
    async runFullDebug() {
        console.log('🚀 Running comprehensive token debug...');
        console.log('='.repeat(50));
        
        // Step 1: Check current status
        console.log('\n1️⃣ Checking current token status:');
        this.checkCurrentToken();
        
        // Step 2: Test validation with current token
        console.log('\n2️⃣ Testing current token validation:');
        const currentToken = localStorage.getItem('api-token') || window.apiToken;
        if (currentToken) {
            await this.testTokenValidation(currentToken);
        } else {
            console.log('No token found to test');
        }
        
        // Step 3: Test with correct token
        console.log('\n3️⃣ Testing with correct token:');
        await this.testTokenValidation(this.correctToken);
        
        // Step 4: Test endpoints
        console.log('\n4️⃣ Testing endpoints:');
        await this.testEndpoints();
        
        console.log('\n='.repeat(50));
        console.log('🏁 Debug complete!');
    }
}

// Make debugger available globally
window.tokenDebugger = new TokenDebugger();

// Add console helpers
console.log('🔧 Token Debug Helper loaded!');
console.log('Available commands:');
console.log('- tokenDebugger.runFullDebug() - Run comprehensive debug');
console.log('- tokenDebugger.testTokenValidation() - Test token validation');
console.log('- tokenDebugger.checkCurrentToken() - Check current token status');
console.log('- tokenDebugger.fixToken() - Set correct token');
console.log('- tokenDebugger.clearToken() - Clear all token data');
console.log('- tokenDebugger.testEndpoints() - Test multiple endpoints');
